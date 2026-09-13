"""
Grading by EXECUTION MATCH.

We do not compare SQL text. Two correct queries can look nothing alike, and a
model that writes `JOIN ... USING (customer_id)` instead of `ON a.x = b.y` is
not wrong. So we run both queries and compare the result sets -- the same
convention BIRD and Spider use.

What "match" means here:

  * Row MULTISET comparison. Order is ignored (a question that does not say
    "ordered by" should not be graded on ordering), but duplicates count.
  * Column NAMES are ignored. `AS n` vs `AS total` is not a correctness issue.
  * Column ORDER is ignored, because a model that returns (count, industry)
    instead of (industry, count) answered the question.
  * Floats and Decimals are rounded before comparing. Without this, DECIMAL vs
    DOUBLE noise (4633998.90 vs 4633998.8999999985) reads as a mismatch and
    the harness gets blamed for a rounding artefact.

This is strict on VALUES and lenient on PRESENTATION, which is the right split.
A candidate that returns the right number plus three extra columns is scored
wrong -- that is deliberate; "answer plus noise" is not an answer.
"""

from __future__ import annotations

import datetime as _dt
import math
from decimal import Decimal
from typing import Any

# Significant digits, NOT decimal places.
#
# A fixed decimal-place rounding is wrong at both ends of the scale. Measured
# failure: Q25's gold query is ROUND(SUM(amount / 1.08), 2) -> 10354399.06,
# and the model computed the same thing without rounding -> 10354399.0648.
# Identical answers, scored as a mismatch, and the harness took the blame.
#
# Rounding to 4 DECIMAL places cannot fix that (the values differ at the 2nd).
# Rounding to fewer decimals would start merging genuinely different counts.
# Significant digits scale with magnitude, so a 7-figure dollar total tolerates
# sub-cent noise while a count of 1490 stays distinct from 1490.4.
#
# Why 7 and not 10: a first pass used 10, which fixed Q25 (magnitude 1e7) but
# still failed Q13 -- gold ROUND(AVG(...), 4) = 197.513 against an unrounded
# 197.5130171, a relative difference of 9e-8. 7 significant digits is roughly a
# 1e-7 relative tolerance: loose enough to absorb "gold applied ROUND(), the
# candidate did not", tight enough that every genuinely wrong answer in this
# question set (25 vs 26, 74 vs 76, 1490 vs 9714, 2.0M vs 11.3M) still fails.
#
# This cuts symmetrically: it does not rescue a single baseline answer, because
# no baseline error is anywhere near this small. If that stops being true, the
# tolerance is too loose and should come back down.
SIG_DIGITS = 7


def _round_sig(x: float, sig: int = SIG_DIGITS) -> float:
    if x == 0 or not math.isfinite(x):
        return 0.0 if x == 0 else x
    return round(x, -int(math.floor(math.log10(abs(x)))) + (sig - 1))


def _scalarize(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return _round_sig(float(v))
    if isinstance(v, int):
        return _round_sig(float(v))
    if isinstance(v, float):
        return _round_sig(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return str(v).strip()


def normalize(rows: list[dict]) -> list[tuple]:
    """Rows -> a sorted multiset of value tuples, insensitive to column naming
    and column order."""
    out: list[tuple] = []
    for r in rows:
        vals = [_scalarize(v) for v in r.values()]
        # Sorting WITHIN the row makes column order irrelevant.
        out.append(tuple(sorted(vals, key=repr)))
    return sorted(out, key=repr)


def matches(gold_rows: list[dict], cand_rows: list[dict]) -> bool:
    return normalize(gold_rows) == normalize(cand_rows)


def explain_mismatch(gold_rows: list[dict], cand_rows: list[dict],
                     limit: int = 3) -> str:
    """A short human-readable diff, for triaging the eval afterwards."""
    g, c = normalize(gold_rows), normalize(cand_rows)
    if g == c:
        return "match"
    if not cand_rows:
        return f"candidate returned 0 rows; gold has {len(g)}"
    if len(g) != len(c):
        return (f"row count differs: gold {len(g)}, candidate {len(c)} "
                f"(gold[0]={g[0] if g else None}, cand[0]={c[0]})")
    diffs = [f"gold={gv} cand={cv}" for gv, cv in zip(g, c) if gv != cv][:limit]
    return "; ".join(diffs) if diffs else "differs"
