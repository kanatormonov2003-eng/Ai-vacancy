from __future__ import annotations
import datetime as _dt, json, secrets, unicodedata

def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)

def now_iso() -> str:
    return now().strftime("%Y-%m-%dT%H:%M:%SZ")

def iso(dt: _dt.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_iso(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None

def days_since(value: str | None) -> float | None:
    d = parse_iso(value)
    if d is None:
        return None
    return (now() - d).total_seconds() / 86400.0

def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"

def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)

def loads(raw, default=None):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default

def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))

def strip_control(text: str, limit: int = 100_000) -> str:
    """Remove control characters/zero-width junk from untrusted text."""
    if not text:
        return ""
    text = text[:limit]
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn") and ch not in "\n\t":
            continue
        out.append(ch)
    return "".join(out).strip()
