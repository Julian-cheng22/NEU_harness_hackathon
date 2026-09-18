"""
M-Schema renderer.

WHY NOT JUST `SHOW CREATE TABLE`?
---------------------------------
The XiYanSQL team benchmarked identical models on raw DDL vs their "M-Schema"
representation and measured a consistent ~4-5 point accuracy gain on BIRD from
the format alone (XiYanSQL-QwenCoder-7B-2504: 62.13% M-Schema vs 57.43% DDL).
That is free accuracy, so the baseline uses raw DDL and the harness uses this.

M-Schema differs from DDL in three ways that matter for a small model:
  * one compact line per column instead of multi-line DDL noise
  * REAL EXAMPLE VALUES inline -- which is how a model discovers that
    invoices.status is 'paid'/'unpaid'/'void' and not 'won'/'lost' (D2)
  * an explicit foreign-key block -- ours is INFERRED, because this schema
    declares no FK constraints at all (D8)

Caveat worth stating out loud: example values are a leak vector. Do not point
this at real customer data without a redaction pass.
"""

from __future__ import annotations

from typing import Any

from .db import DbConfig, connect

# Columns whose sample values are high-cardinality noise: showing three random
# timestamps teaches the model nothing and costs context. Show the range instead.
_RANGE_TYPES = {"date", "datetime", "timestamp", "time", "year"}


def _fetch_columns(conn, database: str) -> dict[str, list[dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name AS tbl, column_name AS col, column_type AS ctype,
                   data_type AS dtype, is_nullable AS nullable,
                   column_key AS ckey, column_comment AS ccomment
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (database,),
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for r in cur.fetchall():
            out.setdefault(r["tbl"], []).append(r)
        return out


def _sample_values(conn, table: str, column: str, dtype: str, n: int = 3) -> str:
    """Return a short, human-readable sample of what actually lives in a column."""
    try:
        with conn.cursor() as cur:
            if dtype in _RANGE_TYPES:
                cur.execute(
                    f"SELECT MIN(`{column}`) AS lo, MAX(`{column}`) AS hi FROM `{table}`"
                )
                r = cur.fetchone() or {}
                if r.get("lo") is None:
                    return "all NULL"
                return f"range {r['lo']} .. {r['hi']}"

            # DISTINCT so we surface the value *domain*, which is the point.
            cur.execute(
                f"SELECT DISTINCT `{column}` AS v FROM `{table}` "
                f"WHERE `{column}` IS NOT NULL LIMIT {n}"
            )
            vals = [r["v"] for r in cur.fetchall()]
            if not vals:
                return "all NULL"
            return ", ".join(repr(v) if isinstance(v, str) else str(v) for v in vals)
    except Exception:
        # A schema card must never be the thing that breaks the run.
        return "?"


def _distinct_count(conn, table: str, column: str) -> int | None:
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(DISTINCT `{column}`) AS n FROM `{table}`")
            return (cur.fetchone() or {}).get("n")
    except Exception:
        return None


def _enumerate_small_domains(conn, table: str, column: str, limit: int = 12) -> list[Any]:
    """If a column has few distinct values, list ALL of them.

    This is what defeats D2 and D9: the model sees every value churn_log
    .reason_code actually takes (1-9), not just the four the COMMENT documents.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT `{column}` AS v, COUNT(*) AS n FROM `{table}` "
                f"WHERE `{column}` IS NOT NULL GROUP BY `{column}` "
                f"ORDER BY n DESC LIMIT {limit + 1}"
            )
            rows = cur.fetchall()
        return [] if len(rows) > limit else [r["v"] for r in rows]
    except Exception:
        return []


def render(cfg: DbConfig | None = None, include_examples: bool = True,
           inferred_joins: list[tuple[str, str]] | None = None) -> str:
    """Render the whole database as an M-Schema card.

    `inferred_joins` comes from harness.joins.infer_joins(); pass it in so the
    foreign-key block is populated. This schema declares no real FKs.
    """
    cfg = cfg or DbConfig()
    lines: list[str] = [f"[DB_ID] {cfg.database}", "[Schema]"]

    with connect(cfg) as conn:
        by_table = _fetch_columns(conn, cfg.database)

        for table, cols in by_table.items():
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
                n_rows = (cur.fetchone() or {}).get("n", 0)

            lines.append(f"# Table: {table}  ({n_rows:,} rows)")
            lines.append("[")
            for c in cols:
                bits = [f"{c['col']}:{c['ctype']}"]
                if c["ckey"] == "PRI":
                    bits.append("Primary Key")
                if c["nullable"] == "YES":
                    bits.append("nullable")

                if include_examples:
                    domain = _enumerate_small_domains(conn, table, c["col"])
                    if domain:
                        # Full domain beats a sample whenever it fits.
                        vals = ", ".join(repr(v) if isinstance(v, str) else str(v)
                                         for v in sorted(domain, key=str))
                        bits.append(f"All values: [{vals}]")
                    else:
                        bits.append(
                            f"Examples: [{_sample_values(conn, table, c['col'], c['dtype'])}]"
                        )

                if c["ccomment"]:
                    # Comments are included because they are useful AND because
                    # one of them is a trap: churn_log.reason_code documents
                    # codes 1-4 while the data holds 1-9 (D9). The "All values"
                    # line above is what lets the model catch the discrepancy.
                    bits.append(f"Comment: {c['ccomment']}")

                lines.append("(" + ", ".join(bits) + "),")
            lines.append("]")

    lines.append("[Foreign keys]")
    if inferred_joins:
        lines.append("# INFERRED -- this database declares no FK constraints.")
        lines.append("# Verify with a LEFT JOIN before trusting any of these.")
        for left, right in inferred_joins:
            lines.append(f"{left} = {right}")
    else:
        lines.append("# None declared, and none inferred yet. Call infer_joins.")

    return "\n".join(lines)


def render_raw_ddl(cfg: DbConfig | None = None) -> str:
    """Baseline representation: what a model gets with NO harness.

    Used by eval/run.py for the control arm. Keep it deliberately unhelpful --
    it is exactly `SHOW CREATE TABLE`, which is what someone would paste in.
    """
    cfg = cfg or DbConfig()
    out: list[str] = []
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name AS t FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
            (cfg.database,),
        )
        tables = [r["t"] for r in cur.fetchall()]
        for t in tables:
            cur.execute(f"SHOW CREATE TABLE `{t}`")
            row = cur.fetchone() or {}
            out.append(row.get("Create Table", ""))
    return ";\n\n".join(out) + ";"
