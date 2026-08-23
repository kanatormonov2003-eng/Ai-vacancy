AI Lead Hunter KG
=================
Python 3.12, stdlib only. SQLite (Postgres not available in the build sandbox).

Run tests:
  PYTHONPATH=. python3 -m unittest discover -s tests

Migrate:
  PYTHONPATH=. python3 -c "from app.db import migrations; print(migrations.migrate())"

Env vars: SECRET_KEY and DATABASE_URL (required in prod), DB_PATH, APP_ENV, HTTP_TIMEOUT_SECONDS,
HTTP_RETRIES, RESPECT_ROBOTS, ALLOW_PRIVATE_HOSTS (must stay 0 in prod),
ENABLED_SOURCES, DIRECTORY_BASE_URL, DIRECTORY_API_KEY, LLM_PROVIDER, LLM_BASE_URL,
LLM_API_KEY, LLM_DAILY_BUDGET_USD, OUTREACH_DAILY_LIMIT, DEMO_WEBSITE_BASE.

Status: foundation + website analyzer + scoring are implemented and tested.
API, UI, jobs, AI and outreach layers are NOT implemented yet.

Production database:
  DATABASE_URL=postgresql://... selects the real psycopg PostgreSQL backend.
  Install `psycopg[binary]` from `requirements-prod.txt`. The application does
  not run migrations automatically; run the versioned migration command during
  deployment and use `/ready` to verify the schema is present.
