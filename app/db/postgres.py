"""Real PostgreSQL DB-API backend.

The driver is optional at import time. Selecting a PostgreSQL URL without an
installed psycopg/psycopg2 driver fails loudly; it never falls back to SQLite.
"""
from __future__ import annotations

import contextlib
import re
import threading
from typing import Any, Iterable, Sequence

from .. import obs
from ..config import load

_local = threading.local()
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CompatRow(dict):
    """Dict row with tuple-style integer access used by the existing repository."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _driver():
    try:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg, dict_row
    except ImportError:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            return psycopg2, RealDictCursor
        except ImportError as exc:
            raise RuntimeError(
                "DATABASE_URL requires psycopg or psycopg2; refusing SQLite fallback"
            ) from exc


def _sql(sql: str) -> str:
    """Convert repository qmarks without touching literals, comments, or dollar quotes."""
    out: list[str] = []
    i = 0
    state = "code"
    dollar_tag: str | None = None

    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if state == "code":
            if ch == "'":
                state = "single"
                out.append(ch)

            elif ch == '"':
                state = "double"
                out.append(ch)

            elif ch == "-" and nxt == "-":
                state = "line_comment"
                out.extend((ch, nxt))
                i += 1

            elif ch == "/" and nxt == "*":
                state = "block_comment"
                out.extend((ch, nxt))
                i += 1

            elif ch == "$":
                marker_end = sql.find("$", i + 1)
                if (
                    marker_end != -1
                    and all(
                        c.isalnum() or c == "_"
                        for c in sql[i + 1 : marker_end]
                    )
                ):
                    dollar_tag = sql[i : marker_end + 1]
                    state = "dollar"
                    out.append(dollar_tag)
                    i = marker_end
                else:
                    out.append(ch)

            elif ch == "?":
                out.append("%s")

            else:
                out.append(ch)

        elif state == "single":
            out.append(ch)

            if ch == "'":
                if nxt == "'":
                    out.append(nxt)
                    i += 1
                else:
                    state = "code"

        elif state == "double":
            out.append(ch)

            if ch == '"':
                if nxt == '"':
                    out.append(nxt)
                    i += 1
                else:
                    state = "code"

        elif state == "line_comment":
            out.append(ch)

            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            out.append(ch)

            if ch == "*" and nxt == "/":
                out.append(nxt)
                i += 1
                state = "code"

        else:
            # PostgreSQL dollar-quoted body.
            if dollar_tag and sql.startswith(dollar_tag, i):
                out.append(dollar_tag)
                i += len(dollar_tag) - 1
                state = "code"
            else:
                out.append(ch)

        i += 1

    return "".join(out)


def conn():
    cfg = load()
    dsn = cfg.database_url

    current = getattr(_local, "conn", None)

    if current is not None and getattr(_local, "dsn", None) == dsn:
        return current

    if current is not None:
        with contextlib.suppress(Exception):
            current.close()

    driver, row_factory = _driver()

    if driver.__name__ == "psycopg":
        connection = driver.connect(dsn, row_factory=row_factory)
        connection.autocommit = True
    else:
        connection = driver.connect(dsn)
        connection.autocommit = True

    _local.conn = connection
    _local.dsn = dsn
    _local.depth = 0

    return connection


def close() -> None:
    connection = getattr(_local, "conn", None)

    if connection is not None:
        with contextlib.suppress(Exception):
            connection.close()

    _local.conn = None
    _local.dsn = None
    _local.depth = 0


def _direct_execute(
    connection,
    sql: str,
    params: Sequence[Any] | dict = (),
):
    """Execute SQL and return the real cursor/result object.

    psycopg3 connections expose execute() directly.
    psycopg2 connections require an explicit cursor().
    """
    if hasattr(connection, "execute"):
        return connection.execute(sql, params)

    cursor = connection.cursor()
    cursor.execute(sql, params)
    return cursor


@contextlib.contextmanager
def tx():
    connection = conn()
    depth = getattr(_local, "depth", 0)

    if depth:
        name = f"sp{depth}"
        _direct_execute(connection, f"SAVEPOINT {name}")

        _local.depth = depth + 1

        try:
            yield connection

        except BaseException:
            _direct_execute(connection, f"ROLLBACK TO SAVEPOINT {name}")
            raise

        else:
            _direct_execute(connection, f"RELEASE SAVEPOINT {name}")

        finally:
            _local.depth = depth

        return

    _local.depth = 1

    try:
        if hasattr(connection, "transaction"):
            with connection.transaction():
                yield connection
        else:
            connection.autocommit = False

            try:
                yield connection
                connection.commit()

            except BaseException:
                connection.rollback()
                raise

            finally:
                connection.autocommit = True

    finally:
        _local.depth = 0


def _rows(cursor) -> list[CompatRow]:
    """Convert a DB cursor into repository-compatible rows."""
    if cursor is None:
        return []

    description = getattr(cursor, "description", None)

    if not description:
        return []

    names = [item[0] for item in description]
    rows = cursor.fetchall()

    result: list[CompatRow] = []

    for row in rows:
        if isinstance(row, dict):
            result.append(CompatRow(row))
        else:
            result.append(CompatRow(zip(names, row)))

    return result


def _execute(
    sql: str,
    params: Sequence[Any] | dict = (),
):
    connection = conn()
    return _direct_execute(connection, _sql(sql), params)


def query(
    sql: str,
    params: Sequence[Any] | dict = (),
) -> list[CompatRow]:
    with obs.timed("db.query_ms"):
        return _rows(_execute(sql, params))


def one(
    sql: str,
    params: Sequence[Any] | dict = (),
) -> CompatRow | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(
    sql: str,
    params: Sequence[Any] | dict = (),
    default: Any = None,
) -> Any:
    row = one(sql, params)
    return default if row is None else row[0]


def execute(
    sql: str,
    params: Sequence[Any] | dict = (),
):
    with obs.timed("db.exec_ms"):
        return _execute(sql, params)


def executemany(
    sql: str,
    seq: Iterable[Sequence[Any]],
) -> None:
    connection = conn()
    values = list(seq)
    converted = _sql(sql)

    if hasattr(connection, "executemany"):
        connection.executemany(converted, values)
        return

    cursor = connection.cursor()
    cursor.executemany(converted, values)


def _ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")

    return name


def insert(table: str, data: dict) -> str | int | None:
    if not data:
        raise ValueError("insert() needs at least one column")

    cols = [_ident(column) for column in data.keys()]
    table_name = _ident(table)

    sql = (
        f"INSERT INTO {table_name} "
        f"({', '.join(cols)}) "
        f"VALUES ({', '.join('%s' for _ in cols)})"
    )

    execute(sql, [data[column] for column in cols])

    return data.get("id")


def update(
    table: str,
    data: dict,
    where: str,
    params: Sequence[Any],
) -> int:
    if not data:
        raise ValueError("update() needs at least one column")

    if not where or not where.strip():
        raise ValueError("update() without a WHERE clause is never intentional")

    table_name = _ident(table)
    sets = ", ".join(
        f"{_ident(key)} = %s"
        for key in data
    )

    cursor = execute(
        f"UPDATE {table_name} SET {sets} WHERE {_sql(where)}",
        [*data.values(), *params],
    )

    return int(cursor.rowcount or 0)


def is_integrity_error(exc: Exception) -> bool:
    driver, _ = _driver()
    cls = getattr(driver, "IntegrityError", ())
    return isinstance(exc, cls)