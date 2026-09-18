"""
Join-graph inference.

This schema declares ZERO foreign keys, on purpose (D8). So the model cannot
read the relationships out of the DDL -- it has to guess, and its guesses are
lexical: it sees `customer_id` and looks for `customer_id`.

That works for subscriptions. It fails for `invoices.cust_id`, and it fails
completely for `deals.acct_id`, which shares no substring with `customer_id`
at all (D1).

So we infer joins from DATA, not names.

WHY CONTAINMENT ALONE IS NOT ENOUGH
-----------------------------------
The obvious metric -- "what fraction of the child's values exist in this
primary key?" -- is badly broken on small integer keys, because small integer
ranges trivially contain each other. Measured on this database, naive
containment produced:

    support_tickets.cust_id -> usage_events.event_id   (100% contained, clean)

which is nonsense, and worse, it REPORTED THE RELATIONSHIP AS CLEAN -- hiding
the orphaned-ticket defect (D8) that the correct parent would have exposed.
It also matched `subscriptions.seats` (a measure, not a key) to
customers.customer_id, simply because seat counts happen to fall in 1..128.

So we require two things:

    containment = |child ∩ parent| / |child|    -- child values must exist
    coverage    = |child ∩ parent| / |parent|   -- and reference a real share
                                                   of the parent's keys

usage_events.event_id has 25,000 keys, of which the tickets reference 0.5%.
Real foreign keys have high coverage; coincidental range overlap does not.

HONEST LIMITATIONS
------------------
* Column naming is used as a weak PRIOR on whether a column is a key at all
  (`*_id`, `*_by`, `*_to`), not to choose which parent it points at. A key
  column named `owner` with low coverage will be missed. Missing a join is
  safe; asserting a wrong one is not.
* Genuinely ambiguous cases exist. `employees.manager_id` references only 6 of
  40 employees, which is statistically indistinguishable from several other
  small keys. Rather than guess, we mark those AMBIGUOUS and hand the model
  every candidate -- telling it to verify is correct behaviour; silently
  picking one is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import DbConfig, connect

# Only columns that can plausibly hold a key.
_KEYLIKE_TYPES = {"int", "bigint", "smallint", "mediumint", "char", "varchar"}

# Suffixes that suggest "this column is a reference to something".
_KEYLIKE_SUFFIXES = ("_id", "_by", "_to", "_ref", "_key", "_fk")

_MIN_CONTAINMENT = 0.60
_MIN_DISTINCT = 5

# A key-like name buys a much lower coverage bar, because a legitimate FK can
# reference only a handful of parent rows (a few managers own everything).
_MIN_COVERAGE_KEYLIKE = 0.10
# A column that does NOT look like a key has to be nearly a perfect subset AND
# cover almost the whole parent before we will believe it. This is what keeps
# `subscriptions.seats` and `churn_log.reason_code` out.
_MIN_COVERAGE_OTHER = 0.85
_MIN_CONTAINMENT_OTHER = 0.98

# Two candidates scoring within this of each other are treated as a tie.
_AMBIGUITY_BAND = 0.15


def _is_keylike_name(col: str) -> bool:
    c = col.lower()
    return c == "id" or c.endswith(_KEYLIKE_SUFFIXES)


@dataclass
class JoinCandidate:
    child: str              # "deals.acct_id"
    parent: str             # "customers.customer_id"
    containment: float      # share of child values found in parent
    coverage: float         # share of parent keys referenced by child
    child_distinct: int
    parent_distinct: int
    orphan_values: int      # distinct child VALUES with no parent row
    orphan_rows: int = 0    # child ROWS affected -- a different number entirely
    alternatives: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.containment * self.coverage

    @property
    def is_clean(self) -> bool:
        return self.orphan_values == 0

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.alternatives)

    def describe(self) -> str:
        pct = f"{self.containment * 100:.1f}%"
        cov = f"{self.coverage * 100:.1f}%"
        line = f"{self.child} -> {self.parent}  [{pct} of child values matched, {cov} of parent keys used]"
        if not self.is_clean:
            # Report ROWS and DISTINCT VALUES separately, and say which is which.
            # Measured failure: this line used to give only the distinct-value
            # count (7). Asked "how many tickets are orphaned?" (answer: 48
            # rows), the model read 7 off this line and reported it. The tool
            # handed it the wrong number in the right-sounding words.
            line += (f"\n      ORPHANS: {self.orphan_rows} ROW(S) of {self.child.split('.')[0]}, "
                     f"spread across {self.orphan_values} distinct {self.child.split('.')[1]} "
                     f"value(s), have NO matching {self.parent} row. "
                     f"If a question asks HOW MANY ROWS, the answer is {self.orphan_rows}, "
                     f"not {self.orphan_values}. "
                     f"An INNER JOIN silently drops those {self.orphan_rows} rows -- use "
                     f"LEFT JOIN and decide explicitly what to do with them.")
        if self.is_ambiguous:
            line += (f"\n      AMBIGUOUS: {', '.join(self.alternatives)} fit the data "
                     f"almost as well. Verify before relying on this.")
        return line


def _primary_keys(conn, database: str) -> list[tuple[str, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name AS tbl, column_name AS col, data_type AS dtype
            FROM information_schema.columns
            WHERE table_schema = %s AND column_key = 'PRI'
            ORDER BY table_name
            """,
            (database,),
        )
        return [(r["tbl"], r["col"], r["dtype"]) for r in cur.fetchall()]


def _candidate_columns(conn, database: str) -> list[tuple[str, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name AS tbl, column_name AS col, data_type AS dtype
            FROM information_schema.columns
            WHERE table_schema = %s AND column_key <> 'PRI'
            ORDER BY table_name, ordinal_position
            """,
            (database,),
        )
        return [(r["tbl"], r["col"], r["dtype"])
                for r in cur.fetchall() if r["dtype"] in _KEYLIKE_TYPES]


def infer_joins(cfg: DbConfig | None = None,
                min_containment: float = _MIN_CONTAINMENT) -> list[JoinCandidate]:
    """Infer the join graph by value containment AND coverage.

    Returns one best candidate per child column, strongest first, with
    genuinely ambiguous cases flagged rather than silently resolved.
    """
    cfg = cfg or DbConfig()
    per_child: dict[str, list[JoinCandidate]] = {}

    with connect(cfg) as conn:
        pks = _primary_keys(conn, cfg.database)
        candidates = _candidate_columns(conn, cfg.database)

        parent_sizes: dict[str, int] = {}
        for ptab, pcol, _ in pks:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(DISTINCT `{pcol}`) AS n FROM `{ptab}`")
                parent_sizes[f"{ptab}.{pcol}"] = int((cur.fetchone() or {}).get("n") or 0)

        for ctab, ccol, cdtype in candidates:
            keylike = _is_keylike_name(ccol)
            for ptab, pcol, pdtype in pks:
                # Self-joins ARE legitimate (employees.manager_id -> employee_id);
                # only skip a column pointing at itself.
                if ctab == ptab and ccol == pcol:
                    continue
                if (cdtype in {"char", "varchar"}) != (pdtype in {"char", "varchar"}):
                    continue

                parent_distinct = parent_sizes.get(f"{ptab}.{pcol}", 0)
                if parent_distinct == 0:
                    continue

                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            SELECT COUNT(DISTINCT c.`{ccol}`) AS child_distinct,
                                   COUNT(DISTINCT p.`{pcol}`) AS matched,
                                   SUM(CASE WHEN p.`{pcol}` IS NULL THEN 1 ELSE 0 END)
                                       AS orphan_rows
                            FROM `{ctab}` c
                            LEFT JOIN `{ptab}` p ON p.`{pcol}` = c.`{ccol}`
                            WHERE c.`{ccol}` IS NOT NULL
                            """
                        )
                        r = cur.fetchone() or {}
                except Exception:
                    continue

                child_distinct = int(r.get("child_distinct") or 0)
                matched = int(r.get("matched") or 0)
                orphan_rows = int(r.get("orphan_rows") or 0)
                if child_distinct < _MIN_DISTINCT:
                    continue

                containment = matched / child_distinct
                coverage = matched / parent_distinct

                if keylike:
                    ok = containment >= min_containment and coverage >= _MIN_COVERAGE_KEYLIKE
                else:
                    ok = (containment >= _MIN_CONTAINMENT_OTHER
                          and coverage >= _MIN_COVERAGE_OTHER)
                if not ok:
                    continue

                per_child.setdefault(f"{ctab}.{ccol}", []).append(JoinCandidate(
                    child=f"{ctab}.{ccol}",
                    parent=f"{ptab}.{pcol}",
                    containment=containment,
                    coverage=coverage,
                    child_distinct=child_distinct,
                    parent_distinct=parent_distinct,
                    orphan_values=child_distinct - matched,
                    orphan_rows=orphan_rows,
                ))

    best: list[JoinCandidate] = []
    for child, cands in per_child.items():
        cands.sort(key=lambda c: (-c.score, -_name_affinity(c), c.parent))
        winner = cands[0]
        # Only a near-tie is ambiguous. A clear winner is reported as fact.
        winner.alternatives = [c.parent for c in cands[1:]
                               if winner.score - c.score <= _AMBIGUITY_BAND]
        best.append(winner)

    return sorted(best, key=lambda j: (-j.score, j.child))


def _name_affinity(jc: JoinCandidate) -> float:
    """Weak tie-break only: prefer a parent whose table name echoes the child
    column. Applied strictly AFTER the data score, so it can separate a tie but
    can never outrank real evidence -- which is the bias this module exists to
    avoid."""
    child_col = jc.child.split(".")[1].lower()
    parent_tab = jc.parent.split(".")[0].lower().rstrip("s")
    return 1.0 if parent_tab[:4] in child_col else 0.0


def as_pairs(candidates: list[JoinCandidate]) -> list[tuple[str, str]]:
    """Shape expected by schema_card.render(inferred_joins=...)."""
    return [(c.child, c.parent) for c in candidates]


def format_for_model(candidates: list[JoinCandidate]) -> str:
    if not candidates:
        return "No join candidates found."
    lines = ["Inferred join graph (from value overlap, NOT declared constraints).",
             "This database declares no foreign keys, so every line below is evidence, not fact.",
             ""]
    lines += [f"  {c.describe()}" for c in candidates]

    dirty = [c for c in candidates if not c.is_clean]
    if dirty:
        # Stated as a fact about the joins, NOT as a task. An earlier version
        # ended with an imperative ("Prefer LEFT JOIN..."), and a 9B treated it
        # as the thing to do: asked for won-deal value by industry, it returned
        # an orphan count instead. Tool output must inform the answer, not
        # compete with the question for the model's attention.
        lines += ["", "Note: these relationships have unmatched rows: "
                  + ", ".join(f"{c.child} ({c.orphan_rows} rows)" for c in dirty)
                  + ". Relevant only if your query joins on them."]
    ambiguous = [c for c in candidates if c.is_ambiguous]
    if ambiguous:
        lines += ["", "AMBIGUOUS (more than one parent fits): "
                  + ", ".join(c.child for c in ambiguous)
                  + ". Do not rely on these without checking."]
    return "\n".join(lines)
