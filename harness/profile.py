"""
Column profiling -- the data-quality probe.

The model cannot see that `usage_events.event_value` is a VARCHAR full of 'N/A',
or that `customers.plan_tier` disagrees with the live subscription tier, by
reading DDL. It has to look at the values. This module is what looks.

Each profile deliberately ends with a plain-language WARNINGS section rather
than raw statistics. A 9B model reliably acts on "SUM() on this column will
silently treat 1,412 junk values as 0" and unreliably acts on "null_rate: 0.07,
non_numeric_rate: 0.056". Write for the reader.

Defects this is designed to surface: D3 (stale copy), D4 (ambiguous NULL),
D5 (type drift), D6 (duplicate entities).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import DbConfig, connect

_NUMERIC_RE = r"^-?[0-9]+(\\.[0-9]+)?$"

# Text columns whose declared type is a string but whose content looks numeric
# are the classic silent-corruption case, so we always check for it.
_TEXT_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext"}


@dataclass
class ColumnProfile:
    table: str
    column: str
    declared_type: str
    total_rows: int
    null_count: int
    distinct_count: int
    samples: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def null_rate(self) -> float:
        return self.null_count / self.total_rows if self.total_rows else 0.0

    def format_for_model(self) -> str:
        lines = [
            f"Profile: {self.table}.{self.column}",
            f"  declared type : {self.declared_type}",
            f"  rows          : {self.total_rows:,}",
            f"  NULLs         : {self.null_count:,} ({self.null_rate * 100:.1f}%)",
            f"  distinct      : {self.distinct_count:,}",
        ]
        for k, v in self.stats.items():
            lines.append(f"  {k:<14}: {v}")
        if self.samples:
            shown = ", ".join(repr(s) if isinstance(s, str) else str(s)
                              for s in self.samples[:8])
            lines.append(f"  sample values : {shown}")
        if self.warnings:
            lines.append("  WARNINGS:")
            lines += [f"    - {w}" for w in self.warnings]
        return "\n".join(lines)


def _column_meta(conn, database: str, table: str, column: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_type AS ctype, data_type AS dtype, column_comment AS ccomment
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s
            """,
            (database, table, column),
        )
        return cur.fetchone() or {}


def profile_column(table: str, column: str, cfg: DbConfig | None = None) -> ColumnProfile:
    cfg = cfg or DbConfig()
    with connect(cfg) as conn:
        meta = _column_meta(conn, cfg.database, table, column)
        if not meta:
            raise ValueError(f"No such column: {table}.{column}")

        dtype = meta["dtype"]
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN `{column}` IS NULL THEN 1 ELSE 0 END) AS nulls,
                       COUNT(DISTINCT `{column}`) AS distinct_n
                FROM `{table}`
                """
            )
            agg = cur.fetchone() or {}

            cur.execute(
                f"SELECT DISTINCT `{column}` AS v FROM `{table}` "
                f"WHERE `{column}` IS NOT NULL LIMIT 8"
            )
            samples = [r["v"] for r in cur.fetchall()]

        p = ColumnProfile(
            table=table, column=column, declared_type=meta["ctype"],
            total_rows=int(agg.get("total") or 0),
            null_count=int(agg.get("nulls") or 0),
            distinct_count=int(agg.get("distinct_n") or 0),
            samples=samples,
        )

        if meta.get("ccomment"):
            p.stats["comment"] = meta["ccomment"]

        _check_type_drift(conn, p, dtype)          # D5
        _check_null_semantics(conn, p, cfg)        # D4
        _check_low_cardinality(conn, p)            # D2, D9
        _check_duplicate_entities(conn, p, dtype)  # D6
        _check_staleness(conn, p, cfg)             # D3

        if p.null_rate > 0.3:
            p.warnings.append(
                f"{p.null_rate * 100:.0f}% of this column is NULL. Aggregates "
                f"ignore NULLs, so AVG/SUM here describe a minority of rows."
            )
    return p


def _check_type_drift(conn, p: ColumnProfile, dtype: str) -> None:
    """D5: numeric data hiding in a text column, with junk mixed in."""
    if dtype not in _TEXT_TYPES:
        return
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
              SUM(CASE WHEN `{p.column}` REGEXP '{_NUMERIC_RE}' THEN 1 ELSE 0 END) AS numeric_n,
              SUM(CASE WHEN `{p.column}` NOT REGEXP '{_NUMERIC_RE}' THEN 1 ELSE 0 END) AS junk_n
            FROM `{p.table}` WHERE `{p.column}` IS NOT NULL
            """
        )
        r = cur.fetchone() or {}
        numeric_n = int(r.get("numeric_n") or 0)
        junk_n = int(r.get("junk_n") or 0)

        if numeric_n == 0:
            return
        p.stats["numeric-looking"] = f"{numeric_n:,}"
        p.stats["non-numeric"] = f"{junk_n:,}"

        if junk_n:
            cur.execute(
                f"SELECT DISTINCT `{p.column}` AS v FROM `{p.table}` "
                f"WHERE `{p.column}` IS NOT NULL AND `{p.column}` NOT REGEXP '{_NUMERIC_RE}' LIMIT 5"
            )
            bad = [r["v"] for r in cur.fetchall()]
            # Be precise about WHICH operations break. An earlier version of
            # this warning said SUM() understates the total; it does not --
            # junk coerces to 0 and zero adds nothing, so SUM is accidentally
            # correct. Overstating the danger trains the model to distrust
            # everything, which is its own failure mode.
            p.warnings.append(
                f"TYPE DRIFT: declared {p.declared_type} but {numeric_n:,} values are "
                f"numeric and {junk_n:,} are not (e.g. {bad!r}), and MySQL coerces "
                f"those to 0 WITHOUT raising an error. Concretely: "
                f"MAX()/MIN() and ORDER BY compare LEXICALLY, so 'N/A' outranks "
                f"'400'; AVG() keeps the {junk_n:,} junk rows in the denominator "
                f"and is biased downward; COUNT(`{p.column}`) counts them. "
                f"(SUM() happens to be safe here, since 0 adds nothing.) "
                f"To be correct in every case, filter with "
                f"`{p.column}` REGEXP '^[0-9]+(\\\\.[0-9]+)?$' first, then CAST."
            )
        else:
            p.warnings.append(
                f"Declared {p.declared_type} but every value is numeric. "
                f"CAST before comparing or ordering, or '9' will sort after '10'."
            )


def _check_null_semantics(conn, p: ColumnProfile, cfg: DbConfig) -> None:
    """D4: a NULL that means two different things.

    Heuristic: if the table has a `status`-like column, check whether NULLs in
    this column span more than one status. If they do, NULL is overloaded.
    """
    if p.null_count == 0:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name AS col FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
              AND column_name IN ('status', 'state', 'stage')
            """,
            (cfg.database, p.table),
        )
        status_cols = [r["col"] for r in cur.fetchall()]
        if not status_cols:
            return
        sc = status_cols[0]
        cur.execute(
            f"SELECT `{sc}` AS s, COUNT(*) AS n FROM `{p.table}` "
            f"WHERE `{p.column}` IS NULL GROUP BY `{sc}` ORDER BY n DESC"
        )
        breakdown = cur.fetchall()

    if len(breakdown) > 1:
        pretty = ", ".join(f"{r['s']}={r['n']:,}" for r in breakdown)
        p.warnings.append(
            f"AMBIGUOUS NULL: rows where {p.column} IS NULL span multiple "
            f"{sc} values ({pretty}). NULL here does NOT cleanly mean one thing. "
            f"Filter on `{sc}` instead of on `{p.column}` IS NULL."
        )


def _check_low_cardinality(conn, p: ColumnProfile) -> None:
    """D2/D9: enumerate the full value domain when it is small, and compare it
    against whatever the column COMMENT claims."""
    if p.distinct_count == 0 or p.distinct_count > 15:
        return
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT `{p.column}` AS v, COUNT(*) AS n FROM `{p.table}` "
            f"WHERE `{p.column}` IS NOT NULL GROUP BY `{p.column}` ORDER BY n DESC"
        )
        rows = cur.fetchall()
    domain = ", ".join(f"{r['v']!r}({r['n']:,})" for r in rows)
    p.stats["full domain"] = domain

    comment = str(p.stats.get("comment", ""))
    if comment:
        undocumented = [str(r["v"]) for r in rows if str(r["v"]) not in comment]
        if undocumented:
            p.warnings.append(
                f"UNDOCUMENTED VALUES: the column comment describes "
                f"{comment!r}, but the data also contains {undocumented}. "
                f"The documentation is INCOMPLETE -- do not infer meanings for "
                f"the undocumented values, and report them as unknown."
            )


def _check_duplicate_entities(conn, p: ColumnProfile, dtype: str) -> None:
    """D6: the same real-world entity under two surrogate keys."""
    if dtype not in _TEXT_TYPES or p.distinct_count < 10:
        return
    if not any(tok in p.column.lower() for tok in ("name", "company", "email", "title")):
        return
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT norm, COUNT(*) AS n FROM (
              SELECT LOWER(REGEXP_REPLACE(`{p.column}`, '[^a-zA-Z0-9]', '')) AS norm
              FROM `{p.table}` WHERE `{p.column}` IS NOT NULL
            ) t GROUP BY norm HAVING COUNT(*) > 1 ORDER BY n DESC LIMIT 5
            """
        )
        dups = cur.fetchall()
    if dups:
        total = sum(int(r["n"]) for r in dups)
        p.warnings.append(
            f"DUPLICATE ENTITIES: {len(dups)} value(s) appear more than once "
            f"after normalising case and punctuation ({total} rows total, "
            f"e.g. {dups[0]['norm']!r}). The same organisation exists under "
            f"multiple keys -- GROUP BY the key and you will split its totals; "
            f"GROUP BY the raw name and you will still split them. Normalise first."
        )


def _check_staleness(conn, p: ColumnProfile, cfg: DbConfig) -> None:
    """D3: a denormalized cache that has drifted from its source of truth.

    Looks for a same-named-or-similar column on another table reachable by an
    obvious key, and measures disagreement.
    """
    # CURATED. `known_pairs` names the exact stale column in this dataset, so a
    # discovery run that could call it would simply be handed D3 rather than
    # finding it. The generic checks above (type drift, duplicates, NULL
    # semantics, value domains) are schema-independent instrumentation and stay
    # on for everyone; only this dataset-specific lookup is gated.
    from . import memory
    if not memory.curated_hints_enabled():
        return

    # The `extra` predicate must use the alias `b` (the parent side), not the
    # bare table name -- the query below aliases both tables, so an unaliased
    # reference is an "Unknown column" error at runtime.
    known_pairs = {
        ("customers", "plan_tier"): (
            "subscriptions", "tier", "customer_id", "customer_id",
            "b.ended_on IS NULL",
        ),
    }
    key = (p.table, p.column)
    if key not in known_pairs:
        # product_catalog.is_current vs effective_to lives entirely in one table.
        if key == ("product_catalog", "is_current"):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM product_catalog "
                    "WHERE (is_current = 1) <> (effective_to IS NULL)"
                )
                n = int((cur.fetchone() or {}).get("n") or 0)
            if n:
                p.warnings.append(
                    f"STALE FLAG: {n} row(s) where is_current disagrees with "
                    f"(effective_to IS NULL). effective_to is authoritative; "
                    f"is_current was left behind by a bad backfill. Use "
                    f"`effective_to IS NULL` to find the live price."
                )
        return

    ptable, pcol, lkey, rkey, extra = known_pairs[key]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM `{p.table}` a
            JOIN `{ptable}` b ON b.`{rkey}` = a.`{lkey}`
            WHERE {extra}
              AND a.`{p.column}` IS NOT NULL AND b.`{pcol}` IS NOT NULL
              AND a.`{p.column}` <> b.`{pcol}`
            """
        )
        n = int((cur.fetchone() or {}).get("n") or 0)
    if n:
        p.warnings.append(
            f"STALE CACHE: {p.table}.{p.column} disagrees with "
            f"{ptable}.{pcol} for {n:,} account(s). {ptable}.{pcol} is the "
            f"source of truth; {p.table}.{p.column} is a cached copy refreshed "
            f"by a nightly job that has been failing. Join to {ptable}."
        )


def profile_table(table: str, cfg: DbConfig | None = None) -> list[ColumnProfile]:
    """Profile every column of a table. Useful for a first pass, but expensive
    in context -- prefer profiling the two or three columns a question needs."""
    cfg = cfg or DbConfig()
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name AS c FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (cfg.database, table),
        )
        cols = [r["c"] for r in cur.fetchall()]
    return [profile_column(table, c, cfg) for c in cols]
