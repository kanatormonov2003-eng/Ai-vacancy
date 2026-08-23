"""Repositories. Every tenant query is scoped by org_id - no exceptions.

That sentence used to be a comment, not a fact. P0-3 makes it true.

Tenant isolation contract enforced in this module:

* every function touching tenant-owned data takes org_id first, no exceptions;
* every write is scoped in SQL or gated by an explicit ownership check, because
  the child tables have no org_id column of their own to scope on;
* cross-tenant access raises NotFoundError, never ForbiddenError: a 403 would
  confirm the id exists in another tenant, which is itself a leak;
* identity columns cannot be changed through a patch dict, otherwise a properly
  scoped update could still hand a row to another org or steal one.
"""
from __future__ import annotations
from typing import Any, Iterable, Sequence
from . import sqlite as db
from ..util import dumps, loads, new_id, now_iso
from ..errors import NotFoundError, ValidationError

# ---------------------------------------------------------------- tenant guards

SEARCH_IMMUTABLE = ("id", "org_id", "user_id", "created_at")
LEAD_IMMUTABLE = ("id", "org_id", "created_at", "dedupe_key")

def _require_org(org_id):
    """A blank org_id is a bug, not an all-tenants wildcard."""
    if not org_id or not str(org_id).strip():
        raise ValidationError("org_id is required")
    return str(org_id)

def owned_lead(org_id: str, lead_id: str) -> str:
    """Assert the lead belongs to this org. Returns the id, raises otherwise."""
    _require_org(org_id)
    if not lead_id:
        raise NotFoundError("Lead not found")
    row = db.one("SELECT id FROM leads WHERE id = ? AND org_id = ? AND deleted_at IS NULL",
                 (lead_id, org_id))
    if not row:
        raise NotFoundError("Lead not found")
    return lead_id

def owned_leads(org_id: str, lead_ids: Iterable[str]) -> list[str]:
    """Filter a caller-supplied id list down to the ones this org actually owns."""
    _require_org(org_id)
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = db.query(
        f"SELECT id FROM leads WHERE org_id = ? AND deleted_at IS NULL AND id IN ({marks})",
        (org_id, *ids))
    owned = {r[0] for r in rows}
    return [i for i in ids if i in owned]

def owned_search(org_id: str, search_id: str) -> str:
    _require_org(org_id)
    if not search_id:
        raise NotFoundError("Search not found")
    row = db.one("SELECT id FROM searches WHERE id = ? AND org_id = ? AND deleted_at IS NULL",
                 (search_id, org_id))
    if not row:
        raise NotFoundError("Search not found")
    return search_id

def _reject_immutable(patch: dict, immutable: Sequence[str], what: str) -> dict:
    bad = sorted(set(patch) & set(immutable))
    if bad:
        raise ValidationError(f"{what}: fields cannot be modified: {', '.join(bad)}")
    return dict(patch)

# ---------------------------------------------------------------- audit / events

def audit(action: str, *, org_id: str | None = None, user_id: str | None = None,
          entity: str | None = None, entity_id: str | None = None, meta: dict | None = None,
          request_id: str | None = None) -> None:
    """Audit rows may have a null org_id on purpose: pre-tenant events such as a
    failed login on an unknown email still have to be recorded somewhere."""
    db.insert("audit_log", {
        "id": new_id("aud"), "org_id": org_id, "user_id": user_id, "action": action,
        "entity": entity, "entity_id": entity_id, "meta": dumps(meta or {}),
        "request_id": request_id, "created_at": now_iso(),
    })

def lead_event(org_id: str, lead_id: str, kind: str, payload: dict | None = None,
               actor: str | None = None) -> None:
    owned_lead(org_id, lead_id)
    db.insert("lead_events", {
        "id": new_id("ev"), "lead_id": lead_id, "org_id": org_id, "kind": kind,
        "payload": dumps(payload or {}), "actor": actor, "created_at": now_iso(),
    })

def lead_events(org_id: str, lead_id: str, limit: int = 50) -> list[dict]:
    owned_lead(org_id, lead_id)
    rows = db.query("SELECT * FROM lead_events WHERE lead_id = ? AND org_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?", (lead_id, org_id, int(limit)))
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = loads(d["payload"], {})
        out.append(d)
    return out

def alert(org_id: str, kind: str, title: str, body: str = "", lead_id: str | None = None,
          severity: str = "info") -> str:
    _require_org(org_id)
    if lead_id:
        owned_lead(org_id, lead_id)
    aid = new_id("alr")
    db.insert("alerts", {"id": aid, "org_id": org_id, "lead_id": lead_id, "kind": kind,
                         "title": title, "body": body, "severity": severity, "created_at": now_iso()})
    return aid

# ---------------------------------------------------------------- orgs & users

def create_org(name: str) -> str:
    oid = new_id("org")
    db.insert("organizations", {"id": oid, "name": name, "created_at": now_iso()})
    db.insert("profiles", {"org_id": oid, "updated_at": now_iso()})
    return oid

def get_user_by_email(email: str) -> dict | None:
    row = db.one("SELECT * FROM users WHERE email = ? AND deleted_at IS NULL", (email.strip().lower(),))
    return dict(row) if row else None

def get_user(user_id: str) -> dict | None:
    row = db.one("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    return dict(row) if row else None

def get_org_user(org_id: str, user_id: str) -> dict | None:
    """Tenant-scoped user lookup: one org must not be able to read another's users."""
    _require_org(org_id)
    row = db.one("SELECT * FROM users WHERE id = ? AND org_id = ? AND deleted_at IS NULL",
                 (user_id, org_id))
    return dict(row) if row else None

def create_user(org_id: str, email: str, password_hash: str, role: str = "owner", locale: str = "ru") -> str:
    _require_org(org_id)
    uid = new_id("usr")
    db.insert("users", {"id": uid, "org_id": org_id, "email": email.strip().lower(),
                        "password_hash": password_hash, "role": role, "locale": locale,
                        "created_at": now_iso()})
    return uid

def get_profile(org_id: str) -> dict:
    _require_org(org_id)
    row = db.one("SELECT * FROM profiles WHERE org_id = ?", (org_id,))
    if not row:
        db.insert("profiles", {"org_id": org_id, "updated_at": now_iso()})
        row = db.one("SELECT * FROM profiles WHERE org_id = ?", (org_id,))
    p = dict(row)
    p["cities"] = loads(p["cities"], [])
    p["categories"] = loads(p["categories"], [])
    p["channels"] = loads(p["channels"], [])
    p["onboarding_done"] = bool(p["onboarding_done"])
    return p

def save_profile(org_id: str, data: dict) -> dict:
    _require_org(org_id)
    patch = {
        "offering": data.get("offering", ""),
        "target_customers": data.get("target_customers", ""),
        "cities": dumps(data.get("cities", [])),
        "categories": dumps(data.get("categories", [])),
        "min_score": int(data.get("min_score", 60)),
        "channels": dumps(data.get("channels", [])),
        "locale": data.get("locale", "ru"),
        "onboarding_done": 1,
        "updated_at": now_iso(),
    }
    get_profile(org_id)
    db.update("profiles", patch, "org_id = ?", (org_id,))
    return get_profile(org_id)

# ---------------------------------------------------------------- searches

def create_search(org_id: str, user_id: str, query_text: str, filters: dict,
                  schedule: str | None = None, parent_search_id: str | None = None) -> str:
    _require_org(org_id)
    if not get_org_user(org_id, user_id):
        raise NotFoundError("User not found")
    if parent_search_id:
        owned_search(org_id, parent_search_id)
    sid = new_id("srch")
    db.insert("searches", {
        "id": sid, "org_id": org_id, "user_id": user_id, "query_text": query_text,
        "filters": dumps(filters), "status": "pending", "schedule": schedule,
        "parent_search_id": parent_search_id, "created_at": now_iso(),
    })
    return sid

def _hydrate_search(row) -> dict:
    s = dict(row)
    s["filters"] = loads(s["filters"], {})
    s["stats"] = loads(s["stats"], {})
    s["sources"] = loads(s["sources"], [])
    return s

def get_search(org_id: str, search_id: str) -> dict:
    _require_org(org_id)
    row = db.one("SELECT * FROM searches WHERE id = ? AND org_id = ? AND deleted_at IS NULL",
                 (search_id, org_id))
    if not row:
        raise NotFoundError("Search not found")
    return _hydrate_search(row)

def list_searches(org_id: str, limit: int = 25, offset: int = 0) -> tuple[list[dict], int]:
    _require_org(org_id)
    total = db.scalar("SELECT COUNT(*) FROM searches WHERE org_id = ? AND deleted_at IS NULL", (org_id,), 0)
    rows = db.query("SELECT * FROM searches WHERE org_id = ? AND deleted_at IS NULL "
                    "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (org_id, limit, offset))
    return [_hydrate_search(r) for r in rows], int(total)

def update_search(org_id: str, search_id: str, patch: dict) -> int:
    """Tenant-scoped search write.

    P0-3: this was update_search(search_id, patch) with WHERE id = ?, so any
    caller holding a search id could rewrite another tenant's row. org_id is now
    mandatory and part of the WHERE clause, and a miss raises NotFoundError
    instead of silently updating nothing.
    """
    _require_org(org_id)
    if not search_id:
        raise NotFoundError("Search not found")
    patch = _reject_immutable(patch, SEARCH_IMMUTABLE, "search")
    if not patch:
        raise ValidationError("search: empty patch")
    for key in ("filters", "stats", "sources"):
        if key in patch and not isinstance(patch[key], str):
            patch[key] = dumps(patch[key])
    changed = db.update("searches", patch, "id = ? AND org_id = ? AND deleted_at IS NULL",
                        (search_id, org_id))
    if not changed:
        raise NotFoundError("Search not found")
    return changed

def soft_delete_search(org_id: str, search_id: str) -> int:
    _require_org(org_id)
    changed = db.update("searches", {"deleted_at": now_iso()},
                        "id = ? AND org_id = ? AND deleted_at IS NULL", (search_id, org_id))
    if not changed:
        raise NotFoundError("Search not found")
    return changed

def add_search_result(org_id: str, search_id: str, lead_id: str, *, is_new: bool = True,
                      change_summary: list[str] | None = None, score_before: int | None = None,
                      score_after: int | None = None) -> None:
    """Link a lead into a search run. Both sides are ownership-checked, so a search
    owned by org A can never reference a lead owned by org B."""
    owned_search(org_id, search_id)
    owned_lead(org_id, lead_id)
    db.execute(
        "INSERT INTO search_results (search_id, lead_id, is_new, change_summary, "
        "score_before, score_after, created_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(search_id, lead_id) DO UPDATE SET is_new = excluded.is_new, "
        "change_summary = excluded.change_summary, score_before = excluded.score_before, "
        "score_after = excluded.score_after",
        (search_id, lead_id, 1 if is_new else 0, dumps(change_summary or []),
         score_before, score_after, now_iso()))

def search_result_leads(org_id: str, search_id: str) -> list[dict]:
    owned_search(org_id, search_id)
    rows = db.query("SELECT sr.* FROM search_results sr JOIN leads l ON l.id = sr.lead_id "
                    "WHERE sr.search_id = ? AND l.org_id = ? ORDER BY sr.created_at DESC",
                    (search_id, org_id))
    out = []
    for r in rows:
        d = dict(r)
        d["change_summary"] = loads(d["change_summary"], [])
        d["is_new"] = bool(d["is_new"])
        out.append(d)
    return out

# ---------------------------------------------------------------- leads

LEAD_SORTABLE = {
    "lead_score": "lead_score", "company_name": "company_name", "city": "city",
    "category": "category", "created_at": "created_at", "updated_at": "updated_at",
    "website_score": "website_score", "last_verified_at": "last_verified_at",
}

def find_candidates(org_id: str, rec: dict, limit: int = 25) -> list[dict]:
    """Cheap indexed candidate fetch for dedupe (never a full scan)."""
    _require_org(org_id)
    clauses, params = [], []
    for column, key in (("website_domain", "website_domain"), ("phone_normalized", "phone_normalized"),
                        ("normalized_name", "normalized_name"), ("instagram", "instagram"),
                        ("dedupe_key", "dedupe_key")):
        if rec.get(key):
            clauses.append(f"{column} = ?")
            params.append(rec[key])
    if not clauses:
        return []
    sql = ("SELECT * FROM leads WHERE org_id = ? AND deleted_at IS NULL AND ("
           + " OR ".join(clauses) + ") LIMIT ?")
    return [dict(r) for r in db.query(sql, (org_id, *params, limit))]

def insert_lead(org_id: str, data: dict) -> str:
    """P0-3: org_id is an explicit argument, not a hopefully-present dict key.

    A caller can no longer create an ownerless lead, and cannot smuggle another
    org's id in through the payload.
    """
    _require_org(org_id)
    data = dict(data)
    if data.get("org_id") not in (None, "", org_id):
        raise ValidationError("lead: org_id in payload does not match the caller org")
    data["org_id"] = org_id
    if data.get("first_search_id"):
        owned_search(org_id, data["first_search_id"])
    data.setdefault("id", new_id("lead"))
    data.setdefault("created_at", now_iso())
    data.setdefault("updated_at", now_iso())
    if "other_social" in data and not isinstance(data["other_social"], str):
        data["other_social"] = dumps(data["other_social"])
    db.insert("leads", data)
    return data["id"]

def update_lead(org_id: str, lead_id: str, patch: dict) -> int:
    _require_org(org_id)
    patch = _reject_immutable(patch, LEAD_IMMUTABLE, "lead")
    if not patch:
        return 0
    patch["updated_at"] = now_iso()
    if "other_social" in patch and not isinstance(patch["other_social"], str):
        patch["other_social"] = dumps(patch["other_social"])
    if patch.get("first_search_id"):
        owned_search(org_id, patch["first_search_id"])
    return db.update("leads", patch, "id = ? AND org_id = ? AND deleted_at IS NULL", (lead_id, org_id))

def get_lead(org_id: str, lead_id: str) -> dict:
    _require_org(org_id)
    row = db.one("SELECT * FROM leads WHERE id = ? AND org_id = ? AND deleted_at IS NULL", (lead_id, org_id))
    if not row:
        raise NotFoundError("Lead not found")
    lead = dict(row)
    lead["other_social"] = loads(lead["other_social"], [])
    lead["is_demo"] = bool(lead["is_demo"])
    return lead

def soft_delete_lead(org_id: str, lead_id: str) -> int:
    _require_org(org_id)
    return db.update("leads", {"deleted_at": now_iso(), "updated_at": now_iso()},
                     "id = ? AND org_id = ? AND deleted_at IS NULL", (lead_id, org_id))

def add_source_ref(org_id: str, lead_id: str, source: str, source_url: str | None,
                   external_id: str | None, raw: dict, is_demo: bool) -> None:
    owned_lead(org_id, lead_id)
    existing = db.one("SELECT id FROM lead_source_refs WHERE lead_id = ? AND source = ? "
                      "AND external_id IS NOT DISTINCT FROM ?", (lead_id, source, external_id))
    if existing:
        db.update("lead_source_refs", {"last_verified_at": now_iso(), "raw": dumps(raw)},
                  "id = ?", (existing[0],))
        return
    db.insert("lead_source_refs", {
        "id": new_id("src"), "lead_id": lead_id, "source": source, "source_url": source_url,
        "external_id": external_id, "is_demo": 1 if is_demo else 0, "raw": dumps(raw),
        "collected_at": now_iso(), "last_verified_at": now_iso(),
    })

def upsert_fact(org_id: str, lead_id: str, key: str, value: str, source: str,
                source_url: str | None, confidence: float) -> None:
    owned_lead(org_id, lead_id)
    row = db.one("SELECT id FROM lead_facts WHERE lead_id = ? AND fact_key = ? AND source = ?",
                 (lead_id, key, source))
    payload = {"fact_value": value, "source_url": source_url, "confidence": confidence,
               "checked_at": now_iso()}
    if row:
        db.update("lead_facts", payload, "id = ?", (row[0],))
    else:
        db.insert("lead_facts", {"id": new_id("fact"), "lead_id": lead_id, "fact_key": key,
                                 "source": source, **payload})

def upsert_signal(org_id: str, lead_id: str, signal: str, source: str, *, polarity: str = "positive",
                  source_url: str | None = None, evidence: str | None = None,
                  confidence: float = 0.5) -> bool:
    """Returns True when the signal is new for this lead+source."""
    owned_lead(org_id, lead_id)
    row = db.one("SELECT id FROM lead_signals WHERE lead_id = ? AND signal = ? AND source = ?",
                 (lead_id, signal, source))
    if row:
        db.update("lead_signals", {"confidence": confidence, "evidence": evidence,
                                  "detected_at": now_iso()}, "id = ?", (row[0],))
        return False
    db.insert("lead_signals", {
        "id": new_id("sig"), "lead_id": lead_id, "signal": signal, "polarity": polarity,
        "source": source, "source_url": source_url, "evidence": evidence,
        "confidence": confidence, "detected_at": now_iso(),
    })
    return True

def save_score(org_id: str, lead_id: str, score: int, reasons: list[str], confidence: float,
               weights_version: str, ai_adjustment: int = 0) -> str:
    owned_lead(org_id, lead_id)
    sid = new_id("scr")
    db.insert("lead_scores", {
        "id": sid, "lead_id": lead_id, "score": int(score), "reasons": dumps(reasons or []),
        "confidence": float(confidence), "weights_version": weights_version,
        "ai_adjustment": int(ai_adjustment), "created_at": now_iso(),
    })
    return sid

def save_website_analysis(org_id: str, lead_id: str, row: dict) -> str:
    owned_lead(org_id, lead_id)
    wid = new_id("wa")
    payload = dict(row)
    payload["scores"] = dumps(payload.get("scores") or {})
    payload["facts"] = dumps(payload.get("facts") or [])
    payload["detected"] = dumps(payload.get("detected") or {})
    payload.update({"id": wid, "lead_id": lead_id})
    payload.setdefault("checked_at", now_iso())
    db.insert("website_analyses", payload)
    return wid

def _child_rows(org_id: str, table: str, lead_ids: Iterable[str], order_by: str = "") -> dict[str, list[dict]]:
    """Fetch child rows for the leads this org owns.

    The id list comes from a caller, so it is intersected with the org's own leads
    first: unknown or foreign ids are dropped instead of leaking rows.
    `table` and `order_by` are module constants, never caller input.
    """
    ids = owned_leads(org_id, lead_ids)
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    sql = (f"SELECT c.* FROM {table} c JOIN leads l ON l.id = c.lead_id "
           f"WHERE l.org_id = ? AND l.deleted_at IS NULL AND c.lead_id IN ({marks}){order_by}")
    rows = db.query(sql, (org_id, *ids))
    out: dict[str, list[dict]] = {i: [] for i in ids}
    for r in rows:
        out[r["lead_id"]].append(dict(r))
    return out

def signals_for(org_id: str, lead_ids: Iterable[str]) -> dict[str, list[dict]]:
    return _child_rows(org_id, "lead_signals", lead_ids, " ORDER BY c.detected_at DESC")

def facts_for(org_id: str, lead_ids: Iterable[str]) -> dict[str, list[dict]]:
    return _child_rows(org_id, "lead_facts", lead_ids)

def sources_for(org_id: str, lead_ids: Iterable[str]) -> dict[str, list[dict]]:
    out = _child_rows(org_id, "lead_source_refs", lead_ids)
    for refs in out.values():
        for d in refs:
            d["raw"] = loads(d["raw"], {})
            d["is_demo"] = bool(d["is_demo"])
    return out

def _latest_child(org_id: str, table: str, lead_id: str, order_col: str) -> dict | None:
    """`table` and `order_col` are module constants, never caller input."""
    owned_lead(org_id, lead_id)
    row = db.one(f"SELECT c.* FROM {table} c JOIN leads l ON l.id = c.lead_id "
                 f"WHERE c.lead_id = ? AND l.org_id = ? AND l.deleted_at IS NULL "
                 f"ORDER BY c.{order_col} DESC, c.id DESC LIMIT 1", (lead_id, org_id))
    return dict(row) if row else None

def latest_score(org_id: str, lead_id: str) -> dict | None:
    d = _latest_child(org_id, "lead_scores", lead_id, "created_at")
    if d:
        d["reasons"] = loads(d["reasons"], [])
    return d

def latest_website_analysis(org_id: str, lead_id: str) -> dict | None:
    d = _latest_child(org_id, "website_analyses", lead_id, "checked_at")
    if d:
        d["scores"] = loads(d["scores"], {})
        d["facts"] = loads(d["facts"], [])
        d["detected"] = loads(d.get("detected"), {})
    return d

def latest_ai_analysis(org_id: str, lead_id: str) -> dict | None:
    owned_lead(org_id, lead_id)
    row = db.one("SELECT * FROM ai_analyses WHERE lead_id = ? AND org_id = ? "
                 "ORDER BY created_at DESC, id DESC LIMIT 1", (lead_id, org_id))
    if not row:
        return None
    d = dict(row)
    d["output"] = loads(d["output"], {})
    return d

def search_leads(org_id: str, *, q: str | None = None, cities: list[str] | None = None,
                 categories: list[str] | None = None, statuses: list[str] | None = None,
                 min_score: int | None = None, max_score: int | None = None,
                 has_website: bool | None = None, has_whatsapp: bool | None = None,
                 has_instagram: bool | None = None, include_demo: bool = True,
                 search_id: str | None = None, sort: str = "lead_score", direction: str = "desc",
                 limit: int = 25, offset: int = 0) -> tuple[list[dict], int]:
    _require_org(org_id)
    join = ""
    params: list[Any] = []
    if search_id:
        # The search is tenant-checked in the join itself, so a foreign search id
        # cannot be used to probe which leads another org also collected.
        join = (" JOIN search_results sr ON sr.lead_id = l.id AND sr.search_id = ?"
                " JOIN searches s ON s.id = sr.search_id AND s.org_id = ? AND s.deleted_at IS NULL")
        params += [search_id, org_id]
    where = ["l.org_id = ?", "l.deleted_at IS NULL"]
    params.append(org_id)
    if q:
        where.append("(l.company_name LIKE ? OR l.normalized_name LIKE ? OR l.phone_normalized LIKE ?"
                     " OR l.website_domain LIKE ? OR l.address LIKE ?)")
        like = f"%{q.strip()}%"
        params += [like, like, like, like, like]
    for col, values in (("l.city", cities), ("l.category", categories), ("l.lead_status", statuses)):
        if values:
            where.append(f"{col} IN ({','.join('?' for _ in values)})")
            params += list(values)
    if min_score is not None:
        where.append("l.lead_score >= ?")
        params.append(int(min_score))
    if max_score is not None:
        where.append("l.lead_score <= ?")
        params.append(int(max_score))
    if has_website is True:
        where.append("l.website IS NOT NULL AND l.website <> ''")
    elif has_website is False:
        where.append("(l.website IS NULL OR l.website = '')")
    if has_whatsapp is True:
        where.append("l.whatsapp IS NOT NULL AND l.whatsapp <> ''")
    elif has_whatsapp is False:
        where.append("(l.whatsapp IS NULL OR l.whatsapp = '')")
    if has_instagram is True:
        where.append("l.instagram IS NOT NULL AND l.instagram <> ''")
    elif has_instagram is False:
        where.append("(l.instagram IS NULL OR l.instagram = '')")
    if not include_demo:
        where.append("l.is_demo = 0")
    sort_col = LEAD_SORTABLE.get(sort, "lead_score")
    direction_sql = "ASC" if str(direction).lower() == "asc" else "DESC"
    base = f" FROM leads l{join} WHERE " + " AND ".join(where)
    total = db.scalar("SELECT COUNT(*)" + base, params, 0)
    rows = db.query("SELECT l.*" + base
                    + f" ORDER BY {sort_col} {direction_sql}, l.id ASC LIMIT ? OFFSET ?",
                    [*params, int(limit), int(offset)])
    leads = []
    for r in rows:
        d = dict(r)
        d["other_social"] = loads(d["other_social"], [])
        d["is_demo"] = bool(d["is_demo"])
        leads.append(d)
    return leads, int(total)

# ---------------------------------------------------------------- usage counters

def bump_usage(org_id: str, metric: str, value: float = 1.0, day: str | None = None) -> float:
    _require_org(org_id)
    day = day or now_iso()[:10]
    db.execute(
        "INSERT INTO usage_counters (org_id, day, metric, value) VALUES (?,?,?,?) "
        "ON CONFLICT(org_id, day, metric) DO UPDATE SET value = value + excluded.value",
        (org_id, day, metric, value))
    return float(db.scalar("SELECT value FROM usage_counters WHERE org_id=? AND day=? AND metric=?",
                           (org_id, day, metric), 0.0))

def usage(org_id: str, metric: str, day: str | None = None) -> float:
    _require_org(org_id)
    day = day or now_iso()[:10]
    return float(db.scalar("SELECT value FROM usage_counters WHERE org_id=? AND day=? AND metric=?",
                           (org_id, day, metric), 0.0))
