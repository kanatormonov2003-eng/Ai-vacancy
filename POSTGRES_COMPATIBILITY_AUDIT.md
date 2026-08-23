# PostgreSQL compatibility hardening audit

Date: 2026-08-23, Asia/Bishkek

## Environment truth

This execution environment did **not** expose `DATABASE_URL` or `TEST_DATABASE_URL`, and did not have psycopg/psycopg2 installed. The repository checkout therefore could not connect to the previously used PostgreSQL instance. PostgreSQL was not simulated with SQLite.

Available local tools included `psql`, `pg_isready`, and `openssl` in the shell listing from the host, but no configured server/DSN was available to the Python process. The conditional PostgreSQL tests correctly remained gated.

## Real bugs fixed

### 1. Null-safe source provenance SQL

```yaml
BUG: external_id IS ? is invalid PostgreSQL syntax after placeholder adaptation
Severity: high
File: app/db/repo.py
Function: add_source_ref()
Root cause: SQLite's IS ? idiom was mechanically translated into PostgreSQL parameter syntax
Fix: replaced it with portable `external_id IS NOT DISTINCT FROM ?`
Impact: nullable source external IDs now have correct equality semantics on SQLite and PostgreSQL
Regression: tests.test_postgres.PostgreSQLAdapterSQLTest.test_null_safe_external_id_sql_is_portable
```

### 2. Unsafe qmark rewriting

```yaml
BUG: the PostgreSQL adapter replaced every `?`, including SQL literals/comments/dollar-quoted bodies
Severity: high
File: app/db/postgres.py
Function: _sql()
Root cause: regex replacement was not SQL-context aware
Fix: quote/comment/dollar-quote aware scanner; only code-state qmarks become `%s`
Regression: test_qmark_adapter_does_not_rewrite_literals_comments_or_dollar_quotes
```

The repository does not use PostgreSQL JSON `?` operators. JSON/text values containing a literal question mark are preserved.

### 3. SQLite-only circuit-breaker insert

```yaml
BUG: INSERT OR IGNORE is not PostgreSQL syntax
Severity: medium
File: app/analysis/circuit.py
Fix: portable INSERT ... ON CONFLICT(provider) DO NOTHING
```

### 4. Backend test contamination

```yaml
BUG: AppTestCase selected PostgreSQL whenever DATABASE_URL remained in the process environment
Severity: high
File: tests/base.py
Fix: SQLite test harness explicitly removes DATABASE_URL; PostgreSQL tests opt in only with TEST_DATABASE_URL
Impact: SQLite unit/runtime tests no longer unexpectedly use PostgreSQL
```

### 5. psycopg connection lifecycle leak

```yaml
BUG: short-lived worker threads could leave thread-local psycopg connections open
Severity: medium
Files: app/runtime.py, app/db/sqlite.py
Fix: process_record closes its owned backend handle in finally; facade closes both backend locals during backend switches and teardown
Verification: python -W error::ResourceWarning -m unittest tests.test_concurrency tests.test_jobs tests.test_worker tests.test_production_flow
Result: 7 tests passed with no ResourceWarning
```

## Concurrency hardening

`tests/test_postgres.py` now exercises `runtime.process_record()` from two threads with the same identity and source provenance. The expected PostgreSQL path is:

`INSERT -> UniqueViolation -> backend integrity detection -> canonical reread -> merge -> source provenance race handling -> created/merged result`.

The code no longer hardcodes `sqlite3.IntegrityError` for runtime decisions; it uses `db.is_integrity_error()` so psycopg exceptions are handled. The actual PostgreSQL conditional test was not executable here because `TEST_DATABASE_URL` and psycopg were absent.

## Jobs compatibility

The existing job implementation was reviewed for PostgreSQL semantics. `claim()` uses `FOR UPDATE SKIP LOCKED` only on PostgreSQL, the queued-row update is guarded by `status='queued'`, lease recovery is bounded, idempotency is protected by the unique key, and retry/fail obey max attempts. SQLite keeps its existing immediate-transaction path.

## Exact verification results

```text
A) python -m unittest tests.test_postgres
   PASS command, 2 adapter tests passed, 2 PostgreSQL integration tests skipped because TEST_DATABASE_URL is absent

B) python -m unittest tests.test_concurrency
   PASS, 1 test

C) python -m unittest tests.test_jobs
   PASS, 3 tests

D) python -m unittest tests.test_worker
   PASS, 2 tests

E) python -m unittest tests.test_production_flow
   PASS, 1 test

F) python -m unittest tests.test_e2e_pipeline
   PASS, 15 tests

G) python -m unittest discover -s tests -t .
   PASS, 115 tests, 2 explicit PostgreSQL skips

H) python -m compileall .
   PASS

   python -c "import app; print('IMPORT OK')"
   PASS

Strict lifecycle check:
   PASS, 7 tests under -W error::ResourceWarning

pytest -vv:
   NOT AVAILABLE, pytest is not installed

pytest --cov=app --cov-report=term-missing:
   NOT AVAILABLE, pytest and coverage are not installed
```

No assertions were weakened. No tests were deleted, xfailed, or skipped beyond the existing explicit PostgreSQL environment gate.

## Changed files

- `app/db/postgres.py`
- `app/db/repo.py`
- `app/db/sqlite.py`
- `app/analysis/circuit.py`
- `app/runtime.py`
- `tests/base.py`
- `tests/test_postgres.py`

## PostgreSQL verdict

```yaml
SQL_COMPATIBILITY_HARDENED: YES
SQL_ADAPTER_TESTED_WITHOUT_NETWORK: YES
REAL_POSTGRES_REVERIFICATION: NOT_EXECUTED
CONCURRENCY_POSTGRES_REVERIFICATION: NOT_EXECUTED
JOBS_POSTGRES_REVERIFICATION: NOT_EXECUTED
```

## Remaining blockers

1. **Verification environment only:** `TEST_DATABASE_URL` and psycopg are absent in this execution context. Minimal action: export the real isolated PostgreSQL URL and install `psycopg[binary]`, then rerun `tests.test_postgres` and the full suite.
2. No code-level PostgreSQL blocker is proven by the available tests, but the real PostgreSQL rerun remains a release requirement.
