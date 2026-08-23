"""Small request boundary: auth -> tenant context -> validation -> rate limit -> action."""
from __future__ import annotations

from ..config import load
from ..domain.query import SearchQuery, parse as parse_query
from ..db import repo
from ..errors import RateLimitError
from .. import runtime as runtime_service
from . import auth, ratelimit, validation


_SORTS = ("lead_score", "company_name", "city", "category", "created_at", "updated_at",
          "website_score", "last_verified_at")
_QUERY_SCHEMA = {
    "q": validation.Field("str", default="", max_len=2000),
    "cities": validation.Field("list", default=list, item="str", max_items=20, max_len=120),
    "categories": validation.Field("list", default=list, item="str", max_items=20, max_len=120),
    "statuses": validation.Field("list", default=list, item="str", max_items=20, max_len=40),
    "min_score": validation.Field("int", default=0, min=0, max=100),
    "max_score": validation.Field("int", default=100, min=0, max=100),
    "has_website": validation.Field("bool", default=None),
    "has_whatsapp": validation.Field("bool", default=None),
    "has_instagram": validation.Field("bool", default=None),
    "include_demo": validation.Field("bool", default=True),
    "search_id": validation.Field("str", default="", max_len=100),
    "sort": validation.Field("str", default="lead_score", choices=_SORTS),
    "direction": validation.Field("str", default="desc", choices=("asc", "desc")),
    "limit": validation.Field("int", default=25, min=1, max=500),
    "offset": validation.Field("int", default=0, min=0, max=1_000_000),
}


def _context(token: str | None) -> dict:
    return auth.resolve_session(token)


def _rate_limit(session: dict, route: str) -> None:
    allowed, retry_after = ratelimit.check(f"{session['id']}:{route}", load().api_rate_per_min)
    if not allowed:
        raise RateLimitError(details={"retry_after_seconds": retry_after})


def query_leads(token: str | None, payload: dict | None = None) -> tuple[list[dict], int]:
    """Query only the authenticated user's org; org_id is never accepted from input."""
    session = _context(token)
    data = validation.validate(payload or {}, _QUERY_SCHEMA)
    _rate_limit(session, "leads.query")
    return _query_for_session(session, data)


def _query_for_session(session: dict, data: dict) -> tuple[list[dict], int]:
    structured = SearchQuery.from_dict(data)
    return repo.search_leads(
        session["org_id"], q=data["q"] or None,
        cities=structured.cities or None, categories=structured.categories or None,
        statuses=data["statuses"] or None, min_score=data["min_score"], max_score=data["max_score"],
        has_website=data["has_website"], has_whatsapp=data["has_whatsapp"],
        has_instagram=data["has_instagram"], include_demo=data["include_demo"],
        search_id=data["search_id"] or None, sort=data["sort"], direction=data["direction"],
        limit=data["limit"], offset=data["offset"],
    )


def ingest_source(token: str | None, source, payload: dict | None = None,
                  *, max_workers: int = 1, analyzer=None):
    """Authenticated source action; source and tenant are server-owned values."""
    session = _context(token)
    data = validation.validate(payload or {}, {
        "text": validation.Field("str", default="", max_len=2000),
        "limit": validation.Field("int", default=50, min=1, max=500),
        "max_workers": validation.Field("int", default=max_workers, min=1, max=32),
    })
    _rate_limit(session, "sources.ingest")
    query = SearchQuery.from_dict({"text": data["text"], "limit": data["limit"]})
    return runtime_service.ingest_source(source, query, session["org_id"],
                                         limit=data["limit"], max_workers=data["max_workers"],
                                         analyzer=analyzer)
