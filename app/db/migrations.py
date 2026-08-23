"""Versioned migrations with real down-migrations (rollback tested)."""
from __future__ import annotations
from . import sqlite as db
from .. import obs

MIGRATIONS: list[tuple[int, str, list[str], list[str]]] = []

def migration(version: int, name: str, up: list[str], down: list[str]) -> None:
    MIGRATIONS.append((version, name, up, down))

migration(1, "core_tenancy_auth", [
    """CREATE TABLE organizations (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        plan TEXT NOT NULL DEFAULT 'free',
        created_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    """CREATE TABLE users (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        email TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'owner' CHECK (role IN ('owner','member','admin')),
        locale TEXT NOT NULL DEFAULT 'ru',
        created_at TEXT NOT NULL,
        last_login_at TEXT,
        deleted_at TEXT
    )""",
    "CREATE UNIQUE INDEX ux_users_email ON users(email)",
    "CREATE INDEX ix_users_org ON users(org_id)",
    """CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        user_agent TEXT
    )""",
    "CREATE UNIQUE INDEX ux_sessions_token ON sessions(token_hash)",
    "CREATE INDEX ix_sessions_user ON sessions(user_id)",
    """CREATE TABLE profiles (
        org_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
        offering TEXT NOT NULL DEFAULT '',
        target_customers TEXT NOT NULL DEFAULT '',
        cities TEXT NOT NULL DEFAULT '[]',
        categories TEXT NOT NULL DEFAULT '[]',
        min_score INTEGER NOT NULL DEFAULT 60 CHECK (min_score BETWEEN 0 AND 100),
        channels TEXT NOT NULL DEFAULT '[]',
        locale TEXT NOT NULL DEFAULT 'ru',
        onboarding_done INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE audit_log (
        id TEXT PRIMARY KEY,
        org_id TEXT,
        user_id TEXT,
        action TEXT NOT NULL,
        entity TEXT,
        entity_id TEXT,
        meta TEXT NOT NULL DEFAULT '{}',
        request_id TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_audit_org_created ON audit_log(org_id, created_at)",
    "CREATE INDEX ix_audit_entity ON audit_log(entity, entity_id)",
], [
    "DROP TABLE audit_log", "DROP TABLE profiles", "DROP TABLE sessions",
    "DROP TABLE users", "DROP TABLE organizations",
])

migration(2, "leads_and_searches", [
    """CREATE TABLE searches (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        query_text TEXT NOT NULL DEFAULT '',
        filters TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','running','completed','failed','cancelled')),
        progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
        stage TEXT NOT NULL DEFAULT 'queued',
        sources TEXT NOT NULL DEFAULT '[]',
        stats TEXT NOT NULL DEFAULT '{}',
        error_code TEXT,
        schedule TEXT CHECK (schedule IS NULL OR schedule IN ('daily','12h','weekly')),
        next_run_at TEXT,
        parent_search_id TEXT REFERENCES searches(id) ON DELETE SET NULL,
        started_at TEXT,
        finished_at TEXT,
        created_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE INDEX ix_searches_org_created ON searches(org_id, created_at)",
    "CREATE INDEX ix_searches_schedule ON searches(schedule, next_run_at)",
    """CREATE TABLE leads (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        dedupe_key TEXT NOT NULL,
        company_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        legal_name TEXT,
        category TEXT,
        subcategory TEXT,
        city TEXT,
        region TEXT,
        country TEXT NOT NULL DEFAULT 'KG',
        phone TEXT,
        phone_normalized TEXT,
        whatsapp TEXT,
        telegram TEXT,
        email TEXT,
        website TEXT,
        website_domain TEXT,
        website_status TEXT NOT NULL DEFAULT 'unknown'
            CHECK (website_status IN ('unknown','not_found','unreachable','ok','error')),
        website_ssl INTEGER,
        website_response_ms INTEGER,
        website_score INTEGER,
        instagram TEXT,
        facebook TEXT,
        telegram_channel TEXT,
        other_social TEXT NOT NULL DEFAULT '[]',
        description TEXT,
        address TEXT,
        employees_estimate INTEGER,
        branches_estimate INTEGER,
        lead_score INTEGER NOT NULL DEFAULT 0 CHECK (lead_score BETWEEN 0 AND 100),
        score_confidence REAL NOT NULL DEFAULT 0,
        lead_status TEXT NOT NULL DEFAULT 'new' CHECK (lead_status IN
            ('new','qualified','ready_to_contact','contacted','replied','interested','negotiation','won','lost','do_not_contact')),
        data_quality_score REAL NOT NULL DEFAULT 0,
        contact_confidence REAL NOT NULL DEFAULT 0,
        website_confidence REAL NOT NULL DEFAULT 0,
        company_match_confidence REAL NOT NULL DEFAULT 1,
        is_demo INTEGER NOT NULL DEFAULT 0,
        first_search_id TEXT REFERENCES searches(id) ON DELETE SET NULL,
        last_seen_at TEXT,
        last_verified_at TEXT,
        analyzed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE UNIQUE INDEX ux_leads_org_dedupe ON leads(org_id, dedupe_key)",
    "CREATE INDEX ix_leads_org_score ON leads(org_id, lead_score)",
    "CREATE INDEX ix_leads_org_status ON leads(org_id, lead_status)",
    "CREATE INDEX ix_leads_org_city ON leads(org_id, city)",
    "CREATE INDEX ix_leads_org_category ON leads(org_id, category)",
    "CREATE INDEX ix_leads_phone ON leads(org_id, phone_normalized)",
    "CREATE INDEX ix_leads_domain ON leads(org_id, website_domain)",
    "CREATE INDEX ix_leads_norm_name ON leads(org_id, normalized_name)",
    """CREATE TABLE lead_source_refs (
        id TEXT PRIMARY KEY,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        source_url TEXT,
        external_id TEXT,
        is_demo INTEGER NOT NULL DEFAULT 0,
        raw TEXT NOT NULL DEFAULT '{}',
        collected_at TEXT NOT NULL,
        last_verified_at TEXT
    )""",
    "CREATE UNIQUE INDEX ux_source_ref ON lead_source_refs(lead_id, source, external_id)",
    "CREATE INDEX ix_source_ref_lead ON lead_source_refs(lead_id)",
    """CREATE TABLE search_results (
        search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        is_new INTEGER NOT NULL DEFAULT 1,
        change_summary TEXT NOT NULL DEFAULT '[]',
        score_before INTEGER,
        score_after INTEGER,
        created_at TEXT NOT NULL,
        PRIMARY KEY (search_id, lead_id)
    )""",
    "CREATE INDEX ix_search_results_lead ON search_results(lead_id)",
], [
    "DROP TABLE search_results", "DROP TABLE lead_source_refs", "DROP TABLE leads", "DROP TABLE searches",
])

migration(3, "analysis_scoring_signals", [
    """CREATE TABLE website_analyses (
        id TEXT PRIMARY KEY,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        url TEXT NOT NULL,
        final_url TEXT,
        reachable INTEGER NOT NULL DEFAULT 0,
        http_status INTEGER,
        https INTEGER NOT NULL DEFAULT 0,
        ssl_valid INTEGER,
        redirects INTEGER NOT NULL DEFAULT 0,
        response_ms INTEGER,
        html_bytes INTEGER,
        scores TEXT NOT NULL DEFAULT '{}',
        total_score INTEGER NOT NULL DEFAULT 0 CHECK (total_score BETWEEN 0 AND 100),
        facts TEXT NOT NULL DEFAULT '[]',
        error_code TEXT,
        checked_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_wa_lead ON website_analyses(lead_id, checked_at)",
    """CREATE TABLE lead_signals (
        id TEXT PRIMARY KEY,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        signal TEXT NOT NULL,
        polarity TEXT NOT NULL DEFAULT 'positive' CHECK (polarity IN ('positive','negative','neutral')),
        source TEXT NOT NULL,
        source_url TEXT,
        evidence TEXT,
        confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
        detected_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX ux_signal_lead ON lead_signals(lead_id, signal, source)",
    """CREATE TABLE lead_scores (
        id TEXT PRIMARY KEY,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
        reasons TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 0,
        weights_version TEXT NOT NULL,
        ai_adjustment INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_scores_lead ON lead_scores(lead_id, created_at)",
    """CREATE TABLE lead_facts (
        id TEXT PRIMARY KEY,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        fact_key TEXT NOT NULL,
        fact_value TEXT NOT NULL,
        source TEXT NOT NULL,
        source_url TEXT,
        confidence REAL NOT NULL DEFAULT 0.5,
        checked_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX ux_fact ON lead_facts(lead_id, fact_key, source)",
    """CREATE TABLE ai_analyses (
        id TEXT PRIMARY KEY,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_name TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        output TEXT NOT NULL DEFAULT '{}',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        degraded INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_ai_lead ON ai_analyses(lead_id, created_at)",
    "CREATE INDEX ix_ai_org_created ON ai_analyses(org_id, created_at)",
    """CREATE TABLE scoring_weights (
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        version TEXT NOT NULL,
        weights TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        PRIMARY KEY (org_id, version)
    )""",
], [
    "DROP TABLE scoring_weights", "DROP TABLE ai_analyses", "DROP TABLE lead_facts",
    "DROP TABLE lead_scores", "DROP TABLE lead_signals", "DROP TABLE website_analyses",
])

migration(4, "outreach_compliance_crm", [
    """CREATE TABLE outreach_messages (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        channel TEXT NOT NULL CHECK (channel IN ('whatsapp','telegram','email','phone','instagram','website_form')),
        target TEXT,
        subject TEXT,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
            ('draft','approved','sent','delivered','replied','interested','not_interested','do_not_contact','failed','skipped')),
        generated_by TEXT NOT NULL DEFAULT 'ai',
        prompt_version TEXT,
        claims TEXT NOT NULL DEFAULT '[]',
        approved_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        approved_at TEXT,
        sent_at TEXT,
        delivery_ref TEXT,
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE INDEX ix_outreach_org_status ON outreach_messages(org_id, status)",
    "CREATE INDEX ix_outreach_lead ON outreach_messages(lead_id, created_at)",
    "CREATE INDEX ix_outreach_sent ON outreach_messages(org_id, sent_at)",
    """CREATE TABLE suppression_list (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('phone','email','domain','lead')),
        value TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX ux_suppression ON suppression_list(org_id, kind, value)",
    """CREATE TABLE lead_events (
        id TEXT PRIMARY KEY,
        lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        actor TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_events_lead ON lead_events(lead_id, created_at)",
    """CREATE TABLE alerts (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        lead_id TEXT REFERENCES leads(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL DEFAULT 'info',
        read_at TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_alerts_org ON alerts(org_id, created_at)",
], [
    "DROP TABLE alerts", "DROP TABLE lead_events", "DROP TABLE suppression_list", "DROP TABLE outreach_messages",
])

migration(5, "jobs_and_usage", [
    """CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        org_id TEXT,
        type TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','running','done','failed','dead')),
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        idempotency_key TEXT,
        run_at TEXT NOT NULL,
        locked_by TEXT,
        locked_at TEXT,
        last_error TEXT,
        result TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX ux_jobs_idem ON jobs(idempotency_key)",
    "CREATE INDEX ix_jobs_claim ON jobs(status, run_at)",
    "CREATE INDEX ix_jobs_org ON jobs(org_id, created_at)",
    """CREATE TABLE http_cache (
        cache_key TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        status INTEGER,
        body TEXT,
        headers TEXT NOT NULL DEFAULT '{}',
        error_code TEXT,
        fetched_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_http_cache_exp ON http_cache(expires_at)",
    """CREATE TABLE provider_health (
        provider TEXT PRIMARY KEY,
        state TEXT NOT NULL DEFAULT 'closed',
        failures INTEGER NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,
        opened_at TEXT,
        last_error TEXT,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE usage_counters (
        org_id TEXT NOT NULL,
        day TEXT NOT NULL,
        metric TEXT NOT NULL,
        value REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (org_id, day, metric)
    )""",
], [
    "DROP TABLE usage_counters", "DROP TABLE provider_health", "DROP TABLE http_cache", "DROP TABLE jobs",
])

migration(6, "website_analysis_detected", [
    "ALTER TABLE website_analyses ADD COLUMN detected TEXT NOT NULL DEFAULT '{}'",
], [
    "ALTER TABLE website_analyses DROP COLUMN detected",
])

def _ensure_table() -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)""")

def applied() -> set[int]:
    _ensure_table()
    return {r[0] for r in db.query("SELECT version FROM schema_migrations")}

def migrate(target: int | None = None) -> list[int]:
    from ..util import now_iso
    _ensure_table()
    done = applied()
    ran = []
    for version, name, up, _down in sorted(MIGRATIONS):
        if version in done or (target is not None and version > target):
            continue
        with db.tx():
            for stmt in up:
                db.execute(stmt)
            db.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?,?,?)",
                       (version, name, now_iso()))
        ran.append(version)
        obs.info("db.migrated", version=version, name=name)
    return ran

def rollback(steps: int = 1) -> list[int]:
    done = sorted(applied(), reverse=True)
    rolled = []
    by_version = {v: (n, u, d) for v, n, u, d in MIGRATIONS}
    for version in done[:steps]:
        name, _up, down = by_version[version]
        with db.tx():
            for stmt in down:
                db.execute(stmt)
            db.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))
        rolled.append(version)
        obs.info("db.rolled_back", version=version, name=name)
    return rolled
