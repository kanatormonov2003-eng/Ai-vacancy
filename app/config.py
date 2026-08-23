"""Central configuration.

Everything comes from the environment; no secrets in the repository.
Every field uses `default_factory` on purpose: dataclass defaults are evaluated
once at import time, which made the process impossible to reconfigure (found by
the analyzer test suite).
"""
from __future__ import annotations
import os, secrets, sys
from dataclasses import dataclass, field
from typing import Any, Callable

def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

def _s(name: str, default: str) -> str:
    return os.environ.get(name, default)

def _env(fn: Callable[[], Any]):
    return field(default_factory=fn)

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "leadhunter.db")

@dataclass(frozen=True)
class Config:
    env: str = _env(lambda: _s("APP_ENV", "development"))
    db_path: str = _env(lambda: _s("DB_PATH", _DEFAULT_DB))
    database_url: str = _env(lambda: _s("DATABASE_URL", ""))
    secret_key: str = _env(lambda: _s("SECRET_KEY", ""))
    host: str = _env(lambda: _s("HOST", "127.0.0.1"))
    port: int = _env(lambda: _i("PORT", 8080))
    session_ttl_s: int = _env(lambda: _i("SESSION_TTL_SECONDS", 43200))
    log_level: str = _env(lambda: _s("LOG_LEVEL", "info"))
    log_json: bool = _env(lambda: _b("LOG_JSON", True))

    # outbound HTTP / website analyzer
    http_timeout_s: float = _env(lambda: _f("HTTP_TIMEOUT_SECONDS", 6.0))
    http_retries: int = _env(lambda: _i("HTTP_RETRIES", 2))
    http_retry_budget: int = _env(lambda: _i("HTTP_RETRY_BUDGET", 50))
    http_max_bytes: int = _env(lambda: _i("HTTP_MAX_BYTES", 2_000_000))
    http_user_agent: str = _env(lambda: _s("HTTP_USER_AGENT", "AILeadHunterKG/1.0 (+contact site owner)"))
    respect_robots: bool = _env(lambda: _b("RESPECT_ROBOTS", True))
    allow_private_hosts: bool = _env(lambda: _b("ALLOW_PRIVATE_HOSTS", False))
    website_cache_ttl_s: int = _env(lambda: _i("WEBSITE_CACHE_TTL_SECONDS", 21600))

    # rate limits & anti-spam
    api_rate_per_min: int = _env(lambda: _i("API_RATE_PER_MIN", 120))
    auth_rate_per_min: int = _env(lambda: _i("AUTH_RATE_PER_MIN", 10))
    outreach_daily_limit: int = _env(lambda: _i("OUTREACH_DAILY_LIMIT", 30))
    outreach_cooldown_days: int = _env(lambda: _i("OUTREACH_COOLDOWN_DAYS", 30))
    search_daily_limit: int = _env(lambda: _i("SEARCH_DAILY_LIMIT", 50))

    # sources
    enabled_sources: tuple = _env(lambda: tuple(x.strip() for x in _s("ENABLED_SOURCES", "demo_kg").split(",") if x.strip()))
    directory_base_url: str = _env(lambda: _s("DIRECTORY_BASE_URL", ""))
    directory_api_key: str = _env(lambda: _s("DIRECTORY_API_KEY", ""))
    source_rate_per_min: int = _env(lambda: _i("SOURCE_RATE_PER_MIN", 60))

    # LLM
    llm_provider: str = _env(lambda: _s("LLM_PROVIDER", "local_rules"))
    llm_fallback: str = _env(lambda: _s("LLM_FALLBACK", "local_rules"))
    llm_base_url: str = _env(lambda: _s("LLM_BASE_URL", ""))
    llm_api_key: str = _env(lambda: _s("LLM_API_KEY", ""))
    llm_model: str = _env(lambda: _s("LLM_MODEL", "gpt-4o-mini"))
    llm_timeout_s: float = _env(lambda: _f("LLM_TIMEOUT_SECONDS", 20.0))
    llm_daily_budget_usd: float = _env(lambda: _f("LLM_DAILY_BUDGET_USD", 5.0))
    llm_per_lead_budget_usd: float = _env(lambda: _f("LLM_PER_LEAD_BUDGET_USD", 0.05))
    llm_max_input_chars: int = _env(lambda: _i("LLM_MAX_INPUT_CHARS", 12000))

    # background jobs
    worker_concurrency: int = _env(lambda: _i("WORKER_CONCURRENCY", 4))
    job_max_attempts: int = _env(lambda: _i("JOB_MAX_ATTEMPTS", 3))
    job_lock_timeout_s: int = _env(lambda: _i("JOB_LOCK_TIMEOUT_SECONDS", 120))

    warnings: tuple = ()

_cached: Config | None = None

def load(force: bool = False) -> Config:
    global _cached
    if _cached is not None and not force:
        return _cached
    warnings: list[str] = []
    env = _s("APP_ENV", "development")
    sk = _s("SECRET_KEY", "")
    database_url = _s("DATABASE_URL", "").strip()
    if not sk:
        if env == "production":
            print("FATAL: SECRET_KEY is required in production", file=sys.stderr)
            raise SystemExit(2)
        sk = secrets.token_hex(32)
        warnings.append("SECRET_KEY not set: using an ephemeral dev key (sessions reset on restart)")
    elif len(sk) < 32 and env == "production":
        print("FATAL: SECRET_KEY must be at least 32 characters in production", file=sys.stderr)
        raise SystemExit(2)
    cfg = Config(secret_key=sk, database_url=database_url, warnings=tuple(warnings))
    if env == "production" and not cfg.database_url:
        print("FATAL: DATABASE_URL is required in production", file=sys.stderr)
        raise SystemExit(2)
    if env == "production" and cfg.allow_private_hosts:
        print("FATAL: ALLOW_PRIVATE_HOSTS must stay off in production (SSRF)", file=sys.stderr)
        raise SystemExit(2)
    parent = os.path.dirname(cfg.db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _cached = cfg
    return cfg

def reset_cache() -> None:
    global _cached
    _cached = None
