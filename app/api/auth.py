"""Authentication: PBKDF2 password hashing, opaque session tokens hashed at rest."""
from __future__ import annotations
import datetime as dt, hashlib, hmac, re, secrets
from ..config import load
from ..db import sqlite as db, repo
from ..errors import AuthError, ValidationError, ConflictError
from ..util import iso, new_id, now, now_iso, parse_iso

PBKDF2_ROUNDS = 210_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[a-zA-Z]{2,}$")
COMMON_PASSWORDS = {"password", "12345678", "qwerty123", "password1", "11111111", "admin123", "123456789"}

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)

def validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise ValidationError("Invalid email address", details={"field": "email"})
    return email

def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 10:
        raise ValidationError("Password must be at least 10 characters", details={"field": "password"})
    if len(password) > 200:
        raise ValidationError("Password too long", details={"field": "password"})
    if password.lower() in COMMON_PASSWORDS:
        raise ValidationError("Password is too common", details={"field": "password"})
    if password.isdigit() or password.isalpha():
        raise ValidationError("Password must mix letters and other characters", details={"field": "password"})
    return password

def _token_hash(token: str) -> str:
    cfg = load()
    return hmac.new(cfg.secret_key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()

def register(email: str, password: str, org_name: str | None = None, locale: str = "ru") -> tuple[str, str]:
    email = validate_email(email)
    validate_password(password)
    with db.tx():
        if repo.get_user_by_email(email):
            raise ConflictError("An account with this email already exists")
        org_id = repo.create_org(org_name or email.split("@")[0])
        user_id = repo.create_user(org_id, email, hash_password(password), locale=locale)
        repo.audit("user.created", org_id=org_id, user_id=user_id, entity="user", entity_id=user_id)
    return user_id, org_id

def login(email: str, password: str, user_agent: str = "") -> tuple[str, dict]:
    email = (email or "").strip().lower()
    user = repo.get_user_by_email(email)
    # constant-ish work regardless of user existence
    stored = user["password_hash"] if user else hash_password(secrets.token_hex(8))
    if not verify_password(password or "", stored) or not user:
        raise AuthError("Invalid email or password")
    token = secrets.token_urlsafe(32)
    cfg = load()
    with db.tx():
        db.insert("sessions", {
            "id": new_id("ses"), "user_id": user["id"], "token_hash": _token_hash(token),
            "created_at": now_iso(), "expires_at": iso(now() + dt.timedelta(seconds=cfg.session_ttl_s)),
            "user_agent": (user_agent or "")[:200],
        })
        db.update("users", {"last_login_at": now_iso()}, "id = ?", (user["id"],))
        repo.audit("user.login", org_id=user["org_id"], user_id=user["id"])
    return token, user

def resolve_session(token: str | None) -> dict:
    if not token:
        raise AuthError()
    row = db.one("""SELECT s.id AS sid, s.expires_at, s.revoked_at, u.*
                    FROM sessions s JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = ?""", (_token_hash(token),))
    if not row:
        raise AuthError()
    data = dict(row)
    if data.get("revoked_at"):
        raise AuthError("Session revoked")
    if data.get("deleted_at"):
        raise AuthError()
    exp = parse_iso(data["expires_at"])
    if exp is None or exp < now():
        raise AuthError("Session expired")
    return data

def logout(token: str | None) -> None:
    if not token:
        return
    db.update("sessions", {"revoked_at": now_iso()}, "token_hash = ? AND revoked_at IS NULL", (_token_hash(token),))

def purge_expired_sessions() -> int:
    cur = db.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    return cur.rowcount
