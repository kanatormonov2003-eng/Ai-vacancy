"""Database access layer.

SQLite is used because the sandbox has no PostgreSQL binary. All SQL is kept to a
portable subset (no SQLite-only syntax in queries, `?` placeholders normalised in
one place) so the engine can be swapped by replacing this module. Concurrency is
handled with WAL + busy_timeout + IMMEDIATE transactions and a bounded retry on
lock contention.
"""
from __future__ import annotations
import contextlib, os, re, sqlite3, threading, time
from typing import Any, Iterable, Sequence
from .. import obs
from ..config import load

_local = threading.local()
_LOCK_RETRIES = 6
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def _pg():
    from . import postgres
    return postgres

def using_postgres() -> bool:
    return bool(load().database_url)

def backend_name() -> str:
    return "postgresql" if using_postgres() else "sqlite"

def is_integrity_error(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    if using_postgres():
        try:
            return _pg().is_integrity_error(exc)
        except RuntimeError:
            return False
    return False

def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn

def _close_local_sqlite() -> None:
    c = getattr(_local, "conn", None)
    if c is not None:
        with contextlib.suppress(Exception):
            c.close()
    _local.conn = None
    _local.path = None
    _local.depth = 0


def conn() -> sqlite3.Connection:
    if using_postgres():
        _close_local_sqlite()
        return _pg().conn()
    # A test process can switch from DATABASE_URL to DB_PATH between classes.
    # Close the opposite thread-local backend before opening SQLite.
    with contextlib.suppress(Exception):
        _pg().close()
    cfg = load()
    existing = getattr(_local, "conn", None)
    if existing is not None and getattr(_local, "path", None) == cfg.db_path:
        return existing
    if existing is not None:
        with contextlib.suppress(Exception):
            existing.close()
    c = _connect(cfg.db_path)
    _local.conn = c
    _local.path = cfg.db_path
    _local.depth = 0
    return c

def close() -> None:
    # Close both possible thread-local backends. This prevents psycopg
    # connections from surviving a backend switch or a test worker thread.
    with contextlib.suppress(Exception):
        _pg().close()
    _close_local_sqlite()

@contextlib.contextmanager
def tx():
    """IMMEDIATE transaction, re-entrant via savepoints, retries on lock."""
    if using_postgres():
        with _pg().tx() as c:
            yield c
        return
    c = conn()
    depth = getattr(_local, "depth", 0)
    if depth:
        name = f"sp{depth}"
        c.execute(f"SAVEPOINT {name}")
        _local.depth = depth + 1
        try:
            yield c
        except BaseException:
            c.execute(f"ROLLBACK TO {name}")
            raise
        else:
            c.execute(f"RELEASE {name}")
        finally:
            _local.depth = depth
        return
    last: Exception | None = None
    for attempt in range(_LOCK_RETRIES):
        try:
            c.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            if "locked" in str(e) or "busy" in str(e):
                last = e
                time.sleep(0.05 * (2 ** attempt))
                continue
            raise
        _local.depth = 1
        try:
            yield c
        except BaseException:
            with contextlib.suppress(Exception):
                c.execute("ROLLBACK")
            raise
        else:
            for commit_attempt in range(_LOCK_RETRIES):
                try:
                    c.execute("COMMIT")
                    break
                except sqlite3.OperationalError as e:  # pragma: no cover - rare
                    if ("locked" in str(e) or "busy" in str(e)) and commit_attempt < _LOCK_RETRIES - 1:
                        time.sleep(0.05 * (2 ** commit_attempt))
                        continue
                    raise
        finally:
            _local.depth = 0
        return
    obs.error("db.lock_timeout", detail=str(last))
    raise last  # type: ignore[misc]

def query(sql: str, params: Sequence[Any] | dict = ()) -> list[sqlite3.Row]:
    if using_postgres():
        return _pg().query(sql, params)
    with obs.timed("db.query_ms"):
        return list(conn().execute(sql, params).fetchall())

def one(sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Row | None:
    if using_postgres():
        return _pg().one(sql, params)
    rows = query(sql, params)
    return rows[0] if rows else None

def scalar(sql: str, params: Sequence[Any] | dict = (), default: Any = None) -> Any:
    if using_postgres():
        return _pg().scalar(sql, params, default)
    row = one(sql, params)
    if row is None:
        return default
    return row[0]

def execute(sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Cursor:
    if using_postgres():
        return _pg().execute(sql, params)
    with obs.timed("db.exec_ms"):
        for attempt in range(_LOCK_RETRIES):
            try:
                return conn().execute(sql, params)
            except sqlite3.OperationalError as e:
                if ("locked" in str(e) or "busy" in str(e)) and attempt < _LOCK_RETRIES - 1:
                    time.sleep(0.05 * (2 ** attempt))
                    continue
                raise
    raise RuntimeError("unreachable")

def executemany(sql: str, seq: Iterable[Sequence[Any]]) -> None:
    if using_postgres():
        return _pg().executemany(sql, seq)
    conn().executemany(sql, list(seq))

def _ident(name: str) -> str:
    """Validate a SQL identifier.

    insert()/update() interpolate table and column names because SQLite has no
    placeholder for identifiers. The names are supposed to come from constants,
    but insert("leads", data) forwards a caller-built dict, so the keys are only
    trusted if they are checked. Anything that is not a plain identifier is a bug
    or an injection attempt; both deserve a hard failure.
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name

def insert(table: str, data: dict) -> str | int:
    if using_postgres():
        return _pg().insert(table, data)
    if not data:
        raise ValueError("insert() needs at least one column")
    cols = [_ident(c) for c in data.keys()]
    sql = f"INSERT INTO {_ident(table)} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    cur = execute(sql, [data[c] for c in cols])
    return data.get("id", cur.lastrowid)

def update(table: str, data: dict, where: str, params: Sequence[Any]) -> int:
    if using_postgres():
        return _pg().update(table, data, where, params)
    if not data:
        raise ValueError("update() needs at least one column")
    if not where or not where.strip():
        raise ValueError("update() without a WHERE clause is never intentional")
    sets = ", ".join(f"{_ident(k)} = ?" for k in data)
    cur = execute(f"UPDATE {_ident(table)} SET {sets} WHERE {where}", [*data.values(), *params])
    return cur.rowcount
