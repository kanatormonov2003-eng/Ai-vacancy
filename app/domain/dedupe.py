"""Deduplication: one canonical lead, many source references.

Matching is deliberately conservative. A high-confidence identifier (registrable
domain, E.164 phone, social handle) can merge on its own; fuzzy names can only
merge with corroborating evidence (same city / address / contact).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from . import normalize as nz

MERGE_THRESHOLD = 0.86
REVIEW_THRESHOLD = 0.70

@dataclass
class MatchResult:
    lead_id: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def should_merge(self) -> bool:
        return self.lead_id is not None and self.confidence >= MERGE_THRESHOLD

    @property
    def needs_review(self) -> bool:
        return self.lead_id is not None and REVIEW_THRESHOLD <= self.confidence < MERGE_THRESHOLD

def dedupe_key(rec: dict) -> str:
    """Stable identity key, strongest available identifier first."""
    domain = nz.registrable_domain(rec.get("website"))
    if domain:
        return f"d:{domain}"
    phone = rec.get("phone_normalized") or nz.normalize_phone(rec.get("phone"))[0]
    if phone:
        return f"p:{phone}"
    insta = (rec.get("instagram") or "").strip().lower().lstrip("@")
    if insta:
        return f"i:{insta}"
    name = rec.get("normalized_name") or nz.normalize_company_name(rec.get("company_name", ""))
    city = (rec.get("city") or "").strip().lower()
    return f"n:{name}|{city}"

def score_pair(incoming: dict, existing: dict) -> MatchResult:
    reasons: list[str] = []
    best = 0.0

    dom_a = nz.registrable_domain(incoming.get("website"))
    dom_b = nz.registrable_domain(existing.get("website"))
    if dom_a and dom_b and dom_a == dom_b:
        best = max(best, 0.97)
        reasons.append(f"same domain {dom_a}")

    ph_a = incoming.get("phone_normalized") or nz.normalize_phone(incoming.get("phone"))[0]
    ph_b = existing.get("phone_normalized") or nz.normalize_phone(existing.get("phone"))[0]
    name_sim = nz.name_similarity(incoming.get("company_name", ""), existing.get("company_name", ""))
    if ph_a and ph_b and ph_a == ph_b:
        if name_sim >= 0.45:
            best = max(best, 0.94)
            reasons.append(f"same phone {ph_a} + similar name ({name_sim})")
        else:
            best = max(best, 0.72)
            reasons.append(f"same phone {ph_a} but different name ({name_sim})")

    for net in ("instagram", "telegram", "facebook"):
        a = (incoming.get(net) or "").lower().lstrip("@")
        b = (existing.get(net) or "").lower().lstrip("@")
        if a and b and a == b:
            best = max(best, 0.95)
            reasons.append(f"same {net} profile @{a}")

    email_a, email_b = nz.normalize_email(incoming.get("email")), nz.normalize_email(existing.get("email"))
    if email_a and email_b and email_a == email_b:
        best = max(best, 0.9)
        reasons.append("same email")

    same_city = bool(incoming.get("city")) and (incoming.get("city") == existing.get("city"))
    addr_sim = nz.address_similarity(incoming.get("address"), existing.get("address"))
    if name_sim >= 0.995 and same_city:
        best = max(best, 0.92)
        reasons.append("identical normalised name + same city")
    elif name_sim >= 0.88 and same_city and addr_sim >= 0.5:
        best = max(best, 0.88)
        reasons.append(f"very similar name ({name_sim}) + same city + similar address ({addr_sim})")
    elif name_sim >= 0.82 and same_city:
        best = max(best, 0.78)
        reasons.append(f"similar name ({name_sim}) + same city")
    elif name_sim >= 0.9:
        best = max(best, 0.74)
        reasons.append(f"similar name ({name_sim}) only")

    # Contradiction guards: distinct strong identifiers must not be merged away.
    if dom_a and dom_b and dom_a != dom_b:
        best = min(best, 0.8)
        reasons.append("different domains (merge held back)")
    if incoming.get("city") and existing.get("city") and incoming["city"] != existing["city"] and not (dom_a and dom_a == dom_b):
        best = min(best, 0.85)
        reasons.append("different cities (merge held back)")

    return MatchResult(existing.get("id"), round(min(best, 0.99), 4), reasons)

def best_match(incoming: dict, candidates: list[dict]) -> MatchResult:
    best = MatchResult()
    for cand in candidates:
        res = score_pair(incoming, cand)
        if res.confidence > best.confidence:
            best = res
    return best

def merge_fields(existing: dict, incoming: dict) -> tuple[dict, list[str]]:
    """Fill gaps without destroying verified data. Returns (patch, change list)."""
    patch: dict = {}
    changes: list[str] = []
    fill_only = [
        "legal_name", "category", "subcategory", "city", "region", "phone", "phone_normalized",
        "whatsapp", "telegram", "email", "website", "website_domain", "instagram", "facebook",
        "telegram_channel", "description", "address", "employees_estimate", "branches_estimate",
    ]
    for key in fill_only:
        new = incoming.get(key)
        if new in (None, "", 0):
            continue
        old = existing.get(key)
        if old in (None, "", 0):
            patch[key] = new
            changes.append(f"{key} added")
        elif key in ("website", "phone", "instagram") and str(old) != str(new):
            changes.append(f"{key} differs across sources (kept existing)")
    for key in ("branches_estimate", "employees_estimate"):
        new, old = incoming.get(key), existing.get(key)
        if isinstance(new, int) and isinstance(old, int) and new > old:
            patch[key] = new
            changes.append(f"{key} increased {old} -> {new}")
    return patch, changes
