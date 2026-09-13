"""
Database access for the harness.

Two connection roles, deliberately separated:

  admin_connect()  -- loading data, running tests. Full privileges.
  connect()        -- what the AGENT uses. Should map to a read-only MySQL
                      account, with a statement timeout and a row cap.

The agent executes SQL that a language model wrote. Treat it as untrusted:
a hallucinated DROP TABLE mid-demo is not a recoverable situation on stage.
Defence is in three layers, because any one of them can be bypassed:

  1. MySQL GRANTs   -- the only layer that actually enforces anything.
  2. sqlglot parse  -- reject anything that is not a single SELECT.
  3. max_execution_time + LIMIT -- stop a runaway from freezing the demo box.

Layer 1 is the real one. Layers 2 and 3 exist so the agent gets a clean,
feed-backable error message instead of a hung connection.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import pymysql
import sqlglot
from sqlglot import exp

# Statements the agent is allowed to run. Anything else is refused before it
# reaches MySQL so the model gets a useful error rather than a permissions blob.
_ALLOWED_STATEMENTS = (exp.Select, exp.Union, exp.Describe, exp.Show)

_BLOCKED_FUNCTIONS = {"load_file", "sleep", "benchmark", "sys_exec"}


class SqlRefused(Exception):
    """Raised before execution when a statement violates the read-only policy."""


@dataclass
class DbConfig:
    host: str = field(default_factory=lambda: os.getenv("DB_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "3306")))
    database: str = field(default_factory=lambda: os.getenv("DB_NAME", "harness"))
    user: str = field(default_factory=lambda: os.getenv("DB_USER", "harness_ro"))
    password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", "readonly"))
    timeout_s: int = field(default_factory=lambda: int(os.getenv("SQL_TIMEOUT_SECONDS", "15")))
    max_rows: int = field(default_factory=lambda: int(os.getenv("SQL_MAX_ROWS", "200")))

    @classmethod
    def admin(cls) -> "DbConfig":
        cfg = cls()
        cfg.user = os.getenv("DB_ADMIN_USER", "root")
        cfg.password = os.getenv("DB_ADMIN_PASSWORD", "")
        return cfg


@contextmanager
def connect(cfg: DbConfig | None = None) -> Iterator[pymysql.connections.Connection]:
    cfg = cfg or DbConfig()
    conn = pymysql.connect(
        host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password,
        database=cfg.database, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=cfg.timeout_s + 5,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def admin_connect() -> Iterator[pymysql.connections.Connection]:
    with connect(DbConfig.admin()) as conn:
        yield conn


def assert_read_only(sql: str) -> exp.Expression:
    """Parse `sql` and refuse anything that could mutate state.

    Returns the parsed AST so callers can reuse it (e.g. to inject a LIMIT)
    instead of parsing twice.
    """
    try:
        statements = sqlglot.parse(sql, dialect="mysql")
    except Exception as e:
        raise SqlRefused(f"Could not parse SQL: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRefused(
            f"Expected exactly one statement, got {len(statements)}. "
            "Multi-statement SQL is not allowed."
        )

    stmt = statements[0]
    if not isinstance(stmt, _ALLOWED_STATEMENTS):
        raise SqlRefused(
            f"Only SELECT queries are allowed here; got {type(stmt).__name__.upper()}. "
            "Rewrite the query as a SELECT."
        )

    # A CTE or subquery can still hide a mutation in some dialects.
    for node in stmt.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop,
                             exp.Create, exp.Alter, exp.TruncateTable)):
            raise SqlRefused(f"Statement contains a {type(node).__name__.upper()}; refused.")
        if isinstance(node, exp.Anonymous) and node.name.lower() in _BLOCKED_FUNCTIONS:
            raise SqlRefused(f"Function {node.name}() is not permitted.")

    return stmt


def _apply_row_cap(stmt: exp.Expression, max_rows: int) -> str:
    """Add a LIMIT if the model did not write one.

    Without this, one `SELECT * FROM usage_events` floods the model's context
    with 25k rows and the agent loop dies on a context-length error.
    """
    if isinstance(stmt, exp.Select) and not stmt.args.get("limit"):
        stmt = stmt.limit(max_rows)
    return stmt.sql(dialect="mysql")


def run_query(sql: str, cfg: DbConfig | None = None) -> dict[str, Any]:
    """Execute a read-only query. Never raises on SQL errors -- returns them in
    the result dict so the agent can read the message and repair the query.

    Returns: {ok, rows, columns, row_count, truncated, sql, error}
    """
    cfg = cfg or DbConfig()
    result: dict[str, Any] = {
        "ok": False, "rows": [], "columns": [], "row_count": 0,
        "truncated": False, "sql": sql, "error": None,
    }

    try:
        stmt = assert_read_only(sql)
        effective_sql = _apply_row_cap(stmt, cfg.max_rows)
    except SqlRefused as e:
        result["error"] = str(e)
        return result

    result["sql"] = effective_sql
    try:
        with connect(cfg) as conn, conn.cursor() as cur:
            cur.execute(f"SET SESSION max_execution_time = {cfg.timeout_s * 1000}")
            cur.execute(effective_sql)
            rows = cur.fetchall()
            result["columns"] = [d[0] for d in (cur.description or [])]
        result["rows"] = rows
        result["row_count"] = len(rows)
        result["truncated"] = len(rows) >= cfg.max_rows
        result["ok"] = True
    except pymysql.MySQLError as e:
        # Hand the raw MySQL message back. "Unknown column 'customer_id' in
        # 'field list'" is exactly the signal the repair loop needs.
        code = e.args[0] if e.args else "?"
        msg = e.args[1] if len(e.args) > 1 else str(e)
        result["error"] = f"MySQL error {code}: {msg}"

    return result


def list_tables(cfg: DbConfig | None = None) -> list[str]:
    cfg = cfg or DbConfig()
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name AS t FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
            (cfg.database,),
        )
        return [r["t"] for r in cur.fetchall()]


READ_ONLY_GRANT_SQL = """
-- Run once as root after loading seed.sql. The agent must not be able to write.
CREATE USER IF NOT EXISTS 'harness_ro'@'%' IDENTIFIED BY 'readonly';
GRANT SELECT ON `{db}`.* TO 'harness_ro'@'%';
GRANT SELECT ON `information_schema`.* TO 'harness_ro'@'%';
FLUSH PRIVILEGES;
"""
