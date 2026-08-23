# LeadHunter deployment runbook

## Local PostgreSQL with Docker Compose

This checkout intentionally contains only one Compose service: PostgreSQL. No application, Redis, or reverse-proxy container is defined.

```bash
cp .env.example .env
# Edit .env and replace POSTGRES_PASSWORD, DATABASE_URL, TEST_DATABASE_URL,
# and SECRET_KEY with local values.

docker compose config
docker compose up -d postgres
docker compose ps
docker compose exec postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

If shell variable expansion is unavailable in the `exec` command, use the values from `.env` explicitly:

```bash
docker compose exec postgres pg_isready -U leadhunter -d leadhunter
```

The service is reachable from the host at `127.0.0.1:${POSTGRES_PORT:-5432}`. The application connection string must use the same host port and credentials:

```text
DATABASE_URL=postgresql://leadhunter:<password>@127.0.0.1:5432/leadhunter
```

The named volume `leadhunter_postgres_data` persists database data across container restarts and `docker compose down`. To remove the data intentionally:

```bash
docker compose down -v
```

## Migrations as a release step

Install the real PostgreSQL driver, never rely on a SQLite fallback:

```bash
python -m pip install -r requirements-prod.txt
export APP_ENV=production
export DATABASE_URL='postgresql://leadhunter:<password>@127.0.0.1:5432/leadhunter'
export SECRET_KEY='<32+ random characters>'
export PYTHONPATH=.
python -c 'from app.db import migrations; print(migrations.migrate())'
```

Migrations are explicit and are not run by application startup. Run the command once against the target database before starting the HTTP process or worker.

## PostgreSQL integration tests

Use an isolated test database, preferably a separate database or PostgreSQL instance. With the Compose database above:

```bash
export TEST_DATABASE_URL='postgresql://leadhunter:<password>@127.0.0.1:5432/leadhunter'
export DATABASE_URL="$TEST_DATABASE_URL"
export APP_ENV=test
export SECRET_KEY='test-secret-key-that-is-long-enough-1234'
export PYTHONPATH=.
python -m unittest tests.test_postgres -v
python -m unittest tests.test_jobs tests.test_worker tests.test_production_flow -v
python -m unittest discover -s tests -t .
```

The PostgreSQL tests are gated by `TEST_DATABASE_URL`; without it they skip rather than claiming verification. Install optional test tooling separately if available:

```bash
pytest -vv
pytest --cov=app --cov-report=term-missing
```

## HTTP and worker launch

WSGI callable:

```text
app.web.server:application
```

Local smoke server:

```bash
PYTHONPATH=. python -m app.web.server
```

Run the worker under a process supervisor in deployment. The worker API is `Worker.run_until_empty()` from `app.jobs.worker`; provide SIGTERM handling around the worker loop and restart policy at the supervisor layer.

## Probes

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/ready
```

`/health` checks process liveness. `/ready` checks database connectivity and the presence of the migration table. Neither endpoint runs migrations.

## Environment

Production requires `APP_ENV=production`, `SECRET_KEY`, and `DATABASE_URL`. Keep `ALLOW_PRIVATE_HOSTS=0`. Configure `API_RATE_PER_MIN`, `AUTH_RATE_PER_MIN`, `WORKER_CONCURRENCY`, `JOB_MAX_ATTEMPTS`, `JOB_LOCK_TIMEOUT_SECONDS`, and existing HTTP/source settings explicitly.
