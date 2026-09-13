"""
SQL execution for the agent: pre-check, run, and sanity-check the result.

The repair loop itself is NOT here. It lives in the agent loop, because a
tool-calling model already has the right shape for it: call execute_sql, read
the error, call it again. Wrapping a second retry loop around that would just
burn the step budget twice as fast.

What this module adds on top of db.run_query:

  * EXPLAIN before running, so a query that would scan 25k rows unindexed
    gets caught before it eats the demo clock.
  * A result sanity check. Zero rows is the failure mode that matters most --
    a model reads "0 rows" as "the answer is zero" or "there is no such data"
    and reports that confidently, when the real cause is usually a value that
    does not exist in that column's domain (D2) or an INNER JOIN that dropped
    everything (D8).
"""

from __future__ import annotations

from typing import Any

from .db import DbConfig, SqlRefused, assert_read_only, connect, run_query

# Columns known to be dirty. If a query touches one, say so in the result --
# the model has usually forgotten by the time it writes the final SQL.
_DIRTY_COLUMNS: dict[str, str] = {
    "event_value": "stored as VARCHAR with 'N/A'/'' junk; MAX/MIN sort lexically and AVG is biased (D5)",
    "paid_at": "NULL means both 'unpaid' and 'paid but unrecorded'; filter on status instead (D4)",
    "plan_tier": "stale cache; subscriptions.tier is the source of truth (D3)",
    "is_current": "unreliable flag; use effective_to IS NULL (D3)",
    "amount": "invoices.amount mixes cents and dollars; deals.amount includes tax (D10)",
    "reason_code": "codes 1-9 exist but only 1-4 are documented (D9)",
    "event_ts": "batch-sourced rows are US/Eastern, everything else UTC (D7)",
    "cust_id": "some values reference deleted customers; INNER JOIN drops them (D8)",
    "company_name": "the same company appears under multiple customer_ids (D6)",
}

_MAX_EXPLAIN_ROWS = 200_000


def explain(sql: str, cfg: DbConfig | None = None) -> dict[str, Any]:
    """Cheap pre-flight. Returns {ok, plan, estimated_rows, error}."""
    cfg = cfg or DbConfig()
    try:
        assert_read_only(sql)
    except SqlRefused as e:
        return {"ok": False, "plan": [], "estimated_rows": 0, "error": str(e)}

    try:
        with connect(cfg) as conn, conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
            plan = list(cur.fetchall())
    except Exception as e:
        return {"ok": False, "plan": [], "estimated_rows": 0, "error": str(e)}

    est = 1
    for step in plan:
        est *= max(int(step.get("rows") or 1), 1)
    return {"ok": True, "plan": plan, "estimated_rows": est, "error": None}


def sanity_check(result: dict[str, Any], sql: str) -> list[str]:
    """Plain-language warnings about a result set. Empty list means nothing
    looked off -- which is not the same as the answer being right."""
    notes: list[str] = []
    if not result.get("ok"):
        return notes

    rows = result.get("rows") or []
    lowered = sql.lower()

    if not rows:
        notes.append(
            "ZERO ROWS. Before reporting this as the answer, check: (a) does the "
            "value you filtered on actually exist in that column? `status` uses a "
            "different vocabulary on invoices (paid/unpaid/void), deals "
            "(open/won/lost) and subscriptions (active/paused/cancelled). "
            "(b) did an INNER JOIN drop everything? Profile the column or try a "
            "LEFT JOIN before concluding the answer is zero."
        )

    # A single NULL aggregate usually means a CAST or filter silently ate
    # everything, not that the true answer is unknown.
    if len(rows) == 1:
        vals = list(rows[0].values())
        if len(vals) == 1 and vals[0] is None:
            notes.append(
                "The single returned value is NULL. An aggregate over zero "
                "matching rows returns NULL -- this is usually a filter or CAST "
                "problem, not a real answer."
            )

    for col, why in _DIRTY_COLUMNS.items():
        if col in lowered:
            notes.append(f"Query touches `{col}`: {why}.")

    if "join" in lowered and "left join" not in lowered and "cust_id" in lowered:
        notes.append(
            "You used an INNER JOIN on `cust_id`. About 5% of support_tickets "
            "reference customers that no longer exist, and those rows are being "
            "silently dropped (D8). Use LEFT JOIN unless you intend to exclude them."
        )

    if result.get("truncated"):
        notes.append(
            f"Result was truncated at {result['row_count']} rows. If you need an "
            f"aggregate, compute it in SQL rather than over this partial list."
        )

    return notes


def execute_sql(sql: str, cfg: DbConfig | None = None,
                skip_explain: bool = False) -> dict[str, Any]:
    """Run a read-only query with a pre-flight check and sanity notes.

    Never raises: errors come back in the dict so the model can read the
    message and repair the query on its next turn.
    """
    cfg = cfg or DbConfig()

    if not skip_explain:
        plan = explain(sql, cfg)
        if not plan["ok"] and plan["error"]:
            return {"ok": False, "rows": [], "columns": [], "row_count": 0,
                    "truncated": False, "sql": sql, "error": plan["error"],
                    "notes": []}
        if plan["ok"] and plan["estimated_rows"] > _MAX_EXPLAIN_ROWS:
            return {
                "ok": False, "rows": [], "columns": [], "row_count": 0,
                "truncated": False, "sql": sql,
                "error": (f"Refused: EXPLAIN estimates ~{plan['estimated_rows']:,} "
                          f"rows examined, which suggests an unconstrained join. "
                          f"Add a join condition or a WHERE clause."),
                "notes": [],
            }

    result = run_query(sql, cfg)
    result["notes"] = sanity_check(result, sql)
    return result


def format_result_for_model(result: dict[str, Any], max_rows: int = 20) -> str:
    """Render a result compactly. Feeding 200 raw dict rows into a 9B model
    wastes context it needs for reasoning."""
    if not result.get("ok"):
        return f"QUERY FAILED\n{result.get('error')}\n\nFix the query and try again."

    rows = result.get("rows") or []
    lines = [f"OK -- {result['row_count']} row(s)"]
    if rows:
        cols = list(rows[0].keys())
        lines.append(" | ".join(cols))
        lines.append("-" * min(72, sum(len(c) + 3 for c in cols)))
        for r in rows[:max_rows]:
            lines.append(" | ".join("NULL" if r[c] is None else str(r[c]) for c in cols))
        if len(rows) > max_rows:
            lines.append(f"... {len(rows) - max_rows} more row(s) not shown")

    for n in result.get("notes") or []:
        lines.append(f"\nNOTE: {n}")
    return "\n".join(lines)
