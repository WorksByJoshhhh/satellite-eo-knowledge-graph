"""
Thin data-access helper around python-oracledb (thin mode: no Oracle Client install needed).
Every database error is converted into DBError with a readable message.
"""
from contextlib import contextmanager

import oracledb

from services import friendly_db_error

oracledb.defaults.fetch_lobs = False   # return CLOB columns (JSON embeddings) as plain strings

_pool = None


class DBError(Exception):
    def __init__(self, code, raw, friendly):
        super().__init__(friendly)
        self.code = code
        self.raw = raw
        self.friendly = friendly


def init_pool(user, password, dsn):
    global _pool
    _pool = oracledb.create_pool(user=user, password=password, dsn=dsn, min=1, max=4, increment=1)
    return _pool


def _wrap(exc):
    err = exc.args[0] if exc.args else None
    code = getattr(err, "code", 0) or 0
    msg = getattr(err, "message", str(exc))
    return DBError(code, msg, friendly_db_error(code, msg))


@contextmanager
def connection():
    if _pool is None:
        raise DBError(0, "pool not initialised",
                      "Database is not configured. Copy .env.example to .env and fill in your Oracle login.")
    try:
        conn = _pool.acquire()
    except oracledb.DatabaseError as exc:
        raise _wrap(exc) from exc
    try:
        yield conn
    except oracledb.DatabaseError as exc:
        conn.rollback()
        raise _wrap(exc) from exc
    finally:
        _pool.release(conn)


def _rows(cur):
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def query(sql, params=None):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        return _rows(cur)


def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql, params=None):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        row = cur.fetchone()
        return row[0] if row else None


def execute(sql, params=None):
    """Run one DML statement and commit. Returns the number of rows affected."""
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        conn.commit()
        return cur.rowcount


def read_dbms_output(cur):
    lines = []
    line, status = cur.var(str), cur.var(int)
    while True:
        cur.callproc("dbms_output.get_line", (line, status))
        if status.getvalue() != 0:
            break
        lines.append(line.getvalue() or "")
    return lines
