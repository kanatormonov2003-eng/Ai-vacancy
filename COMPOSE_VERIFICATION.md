# PostgreSQL Compose verification

## Added

- `compose.yaml`: one PostgreSQL 16 Alpine service, named persistent volume, healthcheck, and environment-driven `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`.
- `.env.example`: PostgreSQL, `DATABASE_URL`, `TEST_DATABASE_URL`, and test secret examples.
- `DEPLOYMENT.md`: exact launch, `pg_isready`, migration, test, HTTP, worker, and environment commands.

## Requested commands

```text
docker compose config                 NOT RUN: docker command not found
docker compose up -d postgres         NOT RUN: docker command not found
docker compose ps                     NOT RUN: docker command not found
docker compose exec postgres pg_isready NOT RUN: docker command not found
```

No PostgreSQL was simulated through SQLite.

## PostgreSQL integration

```text
python -m unittest tests.test_postgres -v
```

Result: 2 tests skipped because `TEST_DATABASE_URL` is not set. The environment also lacks `psycopg`, `psycopg2`, Docker, and PostgreSQL client/server binaries, so no PostgreSQL claim is made.

## Regression

```text
python -m compileall .                         PASS
python -c "import app; print('IMPORT OK')"      PASS
python -m unittest discover -s tests -t .      PASS, 113 tests, 2 explicit PostgreSQL skips
jobs/worker/production_flow tests              PASS, 6 tests
```

`pytest` and coverage were not run because they are unavailable.
