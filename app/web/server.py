"""Small WSGI HTTP adapter. No framework or hidden tenant context.

Mountable with any WSGI server; ``python -m app.web.server`` starts the local
process. Production startup never runs migrations implicitly.
"""
from __future__ import annotations
import json
from http import HTTPStatus
from urllib.parse import parse_qs, unquote
from wsgiref.simple_server import make_server

from .. import obs
from ..api import auth, runtime as api_runtime
from ..config import load
from ..db import migrations, repo, sqlite as db
from ..errors import AppError, NotFoundError, RateLimitError
from ..sources import base as source_base

MAX_BODY = 1_000_000


def _json(start_response, status: int, payload: dict | list):
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    start_response(f"{status} {HTTPStatus(status).phrase}", [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ])
    return [body]


def _token(environ) -> str | None:
    value = environ.get("HTTP_AUTHORIZATION", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _body(environ) -> dict:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise ValueError("invalid content length")
    if length > MAX_BODY:
        raise ValueError("request body too large")
    raw = environ["wsgi.input"].read(length) if length else b"{}"
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


def _query(environ) -> dict:
    q = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    out = {}
    scalar = {"q", "sort", "direction", "search_id", "limit", "offset", "min_score", "max_score",
              "has_website", "has_whatsapp", "has_instagram", "include_demo"}
    for key, values in q.items():
        if key in scalar:
            out[key] = values[-1]
        elif key in {"cities", "categories", "statuses"}:
            out[key] = values
    return out


def _source(name: str):
    source_base.load_all_providers()
    providers = source_base.build([name])
    if not providers:
        raise NotFoundError("Source not found")
    return providers[0]


class Application:
    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = unquote(environ.get("PATH_INFO", "") or "/")
        try:
            if method == "GET" and path == "/health":
                return _json(start_response, 200, {"status": "ok"})
            if method == "GET" and path == "/ready":
                # Readiness is observational. It does not create schema or run migrations.
                db.scalar("SELECT 1")
                db.scalar("SELECT 1 FROM schema_migrations LIMIT 1")
                return _json(start_response, 200, {"status": "ready", "database": db.backend_name()})
            token = _token(environ)
            if method == "GET" and path == "/leads":
                rows, total = api_runtime.query_leads(token, _query(environ))
                return _json(start_response, 200, {"items": rows, "total": total})
            if method == "GET" and path.startswith("/leads/"):
                session = auth.resolve_session(token)
                api_runtime._rate_limit(session, "leads.get")
                lead = repo.get_lead(session["org_id"], path.split("/", 2)[2])
                return _json(start_response, 200, lead)
            if method == "POST" and path.startswith("/ingest/"):
                name = path.split("/", 2)[2]
                source = _source(name)
                payload = _body(environ)
                # Job-backed ingestion is the production action.
                from ..jobs import service as jobs
                session = auth.resolve_session(token)
                # Validate and rate-limit before creating the job, via the same boundary.
                api_runtime._context(token)
                api_runtime._rate_limit(session, "sources.ingest")
                checked = api_runtime.validation.validate(payload, {
                    "text": api_runtime.validation.Field("str", default="", max_len=2000),
                    "limit": api_runtime.validation.Field("int", default=50, min=1, max=500),
                    "max_workers": api_runtime.validation.Field("int", default=1, min=1, max=32),
                })
                idem = environ.get("HTTP_IDEMPOTENCY_KEY", "").strip() or None
                if idem and len(idem) > 200:
                    raise ValueError("idempotency key too long")
                job = jobs.enqueue_ingestion(session["org_id"], name, checked, idempotency_key=idem)
                return _json(start_response, 202, job)
            if method == "GET" and path.startswith("/jobs/"):
                from ..jobs import service as jobs
                session = auth.resolve_session(token)
                api_runtime._rate_limit(session, "jobs.get")
                job = jobs.get_job(session["org_id"], path.split("/", 2)[2])
                return _json(start_response, 200, job)
            return _json(start_response, 404, {"error": {"code": "not_found", "message": "Not found"}})
        except RateLimitError as exc:
            return _json(start_response, 429, {"error": {"code": exc.code, "message": exc.safe_message,
                                                              "details": exc.details}})
        except AppError as exc:
            return _json(start_response, exc.status, {"error": {"code": exc.code, "message": exc.safe_message,
                                                                    "details": exc.details}})
        except (ValueError, json.JSONDecodeError) as exc:
            return _json(start_response, 400, {"error": {"code": "validation_error", "message": "Invalid request"}})
        except Exception as exc:
            if method == "GET" and path == "/ready":
                return _json(start_response, 503, {"error": {"code": "not_ready", "message": "Service not ready"}})
            obs.error("http.request_failed", method=method, path=path, error_type=type(exc).__name__)
            return _json(start_response, 500, {"error": {"code": "internal_error", "message": "Internal error"}})


def application():
    return Application()


def serve(host: str | None = None, port: int | None = None):
    cfg = load()
    with make_server(host or cfg.host, port or cfg.port, application()) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    serve()
