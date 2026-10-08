from contextlib import contextmanager

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

_pool = None


def init_pool(dsn, maxconn):
    global _pool
    _pool = ThreadedConnectionPool(1, maxconn, dsn)


@contextmanager
def transaction():
    conn = _pool.getconn()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
    finally:
        _pool.putconn(conn)


def query(sql, params=None):
    conn = _pool.getconn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    _pool.putconn(conn)
    return rows


def execute(sql, params=None):
    conn = _pool.getconn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    _pool.putconn(conn)
