"""
Verify the gold question set against the live database.

Three things are checked, and the third is the one people forget:

  1. Every `gold_sql` executes without error.
  2. Every `gold_sql` returns a sane result (not empty, unless declared).
  3. Every `naive_sql` returns a DIFFERENT answer than its gold_sql.

(3) is what makes a question worth having. If the naive query happens to
produce the right answer, the question does not discriminate between an
unaided model and a harnessed one, and including it in the eval just dilutes
the measured lift. A trap that does not trap is noise.

    .venv\\Scripts\\python -m pytest tests/test_gold_answers.py -v
    .venv\\Scripts\\python tests/test_gold_answers.py --print   # show answers
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from harness.db import DbConfig, connect  # noqa: E402

SPEC = yaml.safe_load((ROOT / "data" / "questions.yaml").read_text(encoding="utf-8"))
QUESTIONS = SPEC["questions"]

# Questions whose correct answer legitimately has zero rows would go here.
# Empty on purpose: right now every question should return data, and a silently
# empty result is the single most common way a gold answer goes wrong.
ALLOW_EMPTY: set[str] = set()


def _rows(sql: str) -> list[dict]:
    with connect(DbConfig.admin()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _normalize(rows: list[dict]) -> list[tuple]:
    """Compare by row multiset, ignoring column names and ordering.

    Decimals and ints must compare equal (200 == Decimal('200.00')), or every
    aggregate would look like a mismatch.

    Floats are ROUNDED before comparison. Without this, DECIMAL-vs-DOUBLE
    rounding noise reads as a real difference: Q11's naive query returned
    4633998.8999999985 against a gold answer of 4633998.90 and was scored as
    discriminating when the two answers were in fact identical -- which hid the
    fact that the question did not test anything.
    """
    out = []
    for r in rows:
        vals = []
        for v in r.values():
            if isinstance(v, Decimal):
                v = round(float(v), 4)
            elif isinstance(v, bool):
                pass
            elif isinstance(v, int):
                v = float(v)
            elif isinstance(v, float):
                v = round(v, 4)
            vals.append(v)
        out.append(tuple(vals))
    return sorted(out, key=repr)


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_gold_sql_executes(q):
    rows = _rows(q["gold_sql"])
    if q["id"] not in ALLOW_EMPTY:
        assert rows, f"{q['id']} returned no rows -- the gold answer is almost certainly wrong"


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_gold_scalar_is_not_null(q):
    """A scalar gold answer of NULL usually means a bad filter or a failed CAST
    that MySQL swallowed rather than raised."""
    if q.get("answer_type") != "scalar":
        pytest.skip("not a scalar question")
    rows = _rows(q["gold_sql"])
    value = next(iter(rows[0].values()))
    assert value is not None, f"{q['id']} gold answer is NULL"


_WITH_NAIVE = [q for q in QUESTIONS if q.get("naive_sql")]


@pytest.mark.parametrize("q", _WITH_NAIVE, ids=[q["id"] for q in _WITH_NAIVE])
def test_naive_sql_gets_a_different_answer(q):
    """The trap must actually trap. A naive query that errors counts as
    discriminating (the model visibly fails); one that returns the RIGHT answer
    does not, and the question should be dropped or rewritten."""
    gold = _normalize(_rows(q["gold_sql"]))
    try:
        naive = _normalize(_rows(q["naive_sql"]))
    except Exception:
        return  # naive query errors out -- that is a discriminating failure
    assert naive != gold, (
        f"{q['id']}: naive_sql produces the SAME answer as gold_sql, so this "
        f"question cannot distinguish a harnessed model from an unaided one."
    )


def test_every_defect_has_coverage():
    """Each of D1..D10 must be exercised by at least two questions, or its row
    in the per-defect breakdown is a coin flip."""
    counts: dict[str, int] = {}
    for q in QUESTIONS:
        for d in q.get("defect_ids") or []:
            counts[d] = counts.get(d, 0) + 1
    for i in range(1, 11):
        did = f"D{i}"
        assert counts.get(did, 0) >= 2, (
            f"{did} is exercised by {counts.get(did, 0)} question(s); need >= 2"
        )


def test_controls_exist():
    controls = [q for q in QUESTIONS if not q.get("defect_ids")]
    assert len(controls) >= 5, "need >= 5 control questions to detect harness regressions"


def test_question_ids_unique():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)), "duplicate question ids"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args()

    failures = 0
    by_defect: dict[str, int] = {}
    print(f"{len(QUESTIONS)} questions\n")

    for q in QUESTIONS:
        qid = q["id"]
        tags = ",".join(q.get("defect_ids") or []) or "control"
        try:
            rows = _rows(q["gold_sql"])
        except Exception as e:
            print(f"  ERROR {qid:<5} [{tags:<8}] {e}")
            failures += 1
            continue

        for d in q.get("defect_ids") or []:
            by_defect[d] = by_defect.get(d, 0) + 1

        if not rows:
            print(f"  EMPTY {qid:<5} [{tags:<8}] {q['question'][:55]}")
            failures += 1
            continue

        if args.show:
            if q.get("answer_type") == "scalar":
                ans = next(iter(rows[0].values()))
            else:
                ans = f"{len(rows)} row(s): {rows[0]}"
            print(f"  {qid:<5} [{tags:<8}] {str(ans)[:60]:<62} {q['question'][:50]}")
        else:
            print(f"  OK    {qid:<5} [{tags:<8}] {len(rows)} row(s)")

        # Does the trap actually trap?
        if q.get("naive_sql"):
            try:
                naive = _normalize(_rows(q["naive_sql"]))
                if naive == _normalize(rows):
                    print(f"        !! naive_sql matches gold -- question does not discriminate")
                    failures += 1
                elif args.show:
                    n = naive[0][0] if naive and naive[0] else "(empty)"
                    print(f"        naive -> {str(n)[:60]}")
            except Exception as e:
                if args.show:
                    print(f"        naive -> ERROR ({str(e)[:60]})")

    print(f"\nper-defect coverage: "
          + ", ".join(f"{d}={by_defect.get(d, 0)}" for d in [f"D{i}" for i in range(1, 11)]))
    print(f"controls: {sum(1 for q in QUESTIONS if not q.get('defect_ids'))}")
    print(f"\n{failures} problem(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
