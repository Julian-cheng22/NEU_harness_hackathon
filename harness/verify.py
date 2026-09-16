"""
Mechanical verification of a proposed claim.

THE POINT
---------
A claim about a database is falsifiable in a way that a claim about the world
is not: "customers.plan_tier disagrees with subscriptions.tier for 25 accounts"
is one query away from being settled. That property is the only reason the
discovery loop in `discover.py` is trustworthy at all. Everything here exists
to make sure the query actually settles it.

THE CONTRACT
------------
A proposal must supply a read-only SELECT returning exactly one row with one
column named `evidence`, holding the COUNT of rows that exhibit the problem.
Narrow, but narrow is the point -- a free-text "expectation" checked by a model
would just move the trust problem one level up.

WHAT WE ARE DEFENDING AGAINST
-----------------------------
The model is being asked to produce claims AND the proof of those claims. That
is an obvious incentive to write a query that cannot fail. Three shapes turned
up in practice and each has a check below:

  1. Constant proof      SELECT 1 AS evidence
                         -> no table referenced. Rejected.
  2. Tautological proof  SELECT COUNT(*) AS evidence FROM customers
                         -> true of any non-empty table, discriminates nothing.
                         Rejected for having no predicate.
  3. Off-topic proof     a valid, discriminating query about columns the claim
                         never mentions -> proves something real, but not the
                         claim. Rejected.

A fourth shape is not rejected but is FLAGGED: evidence equal to the full row
count of the table. That can be legitimate ("every value in this column is
non-numeric"), so killing it would lose real findings -- but it is also what a
dressed-up tautology looks like, so a human should see it.

None of this makes a verified claim TRUE. It makes it CHECKED: a specific query
returned a specific number, and both are written down next to the claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from .db import DbConfig, SqlRefused, assert_read_only, connect, list_tables

# A proof has to discriminate. These are the constructs that can narrow a
# result to "the rows exhibiting the problem"; a query with none of them is
# counting the whole table and calling it evidence.
_DISCRIMINATORS = (exp.Where, exp.Having, exp.Case, exp.Join, exp.Distinct,
                   exp.Group, exp.If)


@dataclass
class Verdict:
    ok: bool
    evidence: float | None = None
    reason: str = ""
    flags: list[str] | None = None

    def flag_list(self) -> list[str]:
        return self.flags or []


def _referenced_tables(stmt: exp.Expression) -> set[str]:
    return {t.name.lower() for t in stmt.find_all(exp.Table) if t.name}


def _referenced_identifiers(sql: str) -> set[str]:
    """Every bare word in the query, lowercased.

    Deliberately crude rather than AST-based: we only need to ask "did the
    proof mention the column the claim is about", and a column can legitimately
    appear inside a string literal, a REGEXP, or a function name the parser
    renders as Anonymous.
    """
    return set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sql.lower()))


def verify_claim(verification_sql: str, columns: list[str],
                 cfg: DbConfig | None = None) -> Verdict:
    """Run a proposal's proof and decide whether it proves anything.

    `columns` are the claim's own columns, as "table.column" or bare "column".
    """
    cfg = cfg or DbConfig()
    sql = (verification_sql or "").strip().rstrip(";")
    if not sql:
        return Verdict(False, reason="no verification_sql supplied")

    # --- 1. read-only, single statement -----------------------------------
    try:
        stmt = assert_read_only(sql)
    except SqlRefused as e:
        return Verdict(False, reason=f"refused: {e}")
    except Exception as e:                      # sqlglot can raise oddities
        return Verdict(False, reason=f"unparseable: {e}")

    # --- 2. must touch a real table ---------------------------------------
    live = {t.lower() for t in list_tables(cfg)}
    touched = _referenced_tables(stmt) & live
    if not touched:
        return Verdict(False, reason="proof references no table in this database "
                                     "(a constant cannot be evidence)")

    # --- 3. must discriminate ---------------------------------------------
    if not any(stmt.find(node) for node in _DISCRIMINATORS):
        return Verdict(False, reason="proof has no WHERE/HAVING/CASE/JOIN/GROUP BY, "
                                     "so it counts the whole table and shows nothing")

    # --- 4. must be about the claimed columns ------------------------------
    bare = {c.split(".")[-1].strip().lower() for c in columns if c and c.strip()}
    if bare:
        words = _referenced_identifiers(sql)
        if not (bare & words):
            return Verdict(False, reason=f"proof does not mention any claimed column "
                                         f"({', '.join(sorted(bare))})")

    # --- 5. run it ---------------------------------------------------------
    try:
        with connect(cfg) as conn, conn.cursor() as cur:
            cur.execute(f"SET SESSION max_execution_time = {cfg.timeout_s * 1000}")
            cur.execute(sql)
            rows = list(cur.fetchall())
    except Exception as e:
        return Verdict(False, reason=f"proof failed to execute: {e}")

    if len(rows) != 1:
        return Verdict(False, reason=f"proof returned {len(rows)} rows; the contract "
                                     f"is exactly 1 row named `evidence`")

    row: dict[str, Any] = rows[0]
    key = next((k for k in row if k.lower() == "evidence"), None)
    if key is None:
        return Verdict(False, reason=f"proof returned columns {list(row)}; "
                                     f"the contract is one column named `evidence`")

    try:
        value = float(row[key])
    except (TypeError, ValueError):
        return Verdict(False, reason=f"`evidence` was {row[key]!r}, which is not a number")

    if value <= 0:
        return Verdict(False, evidence=value,
                       reason="`evidence` is 0 -- the proof ran and found nothing, "
                              "so the claim is disproved by its own query")

    # --- 6. flag, do not reject, a full-table result -----------------------
    flags: list[str] = []
    for t in sorted(touched):
        try:
            with connect(cfg) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM `{t}`")
                total = int((cur.fetchone() or {}).get("n") or 0)
        except Exception:
            continue
        if total and value >= total:
            flags.append(f"evidence ({value:.0f}) covers every row of `{t}` "
                         f"({total}) -- legitimate for a whole-column property, "
                         f"but also what a disguised tautology looks like")
            break

    return Verdict(True, evidence=value, reason="proof ran and discriminated", flags=flags)


def sanity_check_sql(sql: str) -> list[str]:
    """Cheap static observations about a proposal's *guidance* SQL snippet.

    The guidance is a hint the small model will later read, not something we
    execute, so it cannot be verified -- but an obviously broken hint is worth
    surfacing to the human reviewer rather than shipping silently.
    """
    notes: list[str] = []
    if not sql.strip():
        return notes
    try:
        sqlglot.parse(sql, dialect="mysql")
    except Exception:
        notes.append("guidance SQL does not parse standalone (may be a fragment)")
    return notes
