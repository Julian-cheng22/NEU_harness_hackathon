"""
Run every defect's `detect_sql` from data/defects.yaml against the LOADED
database, and assert the anomaly is actually present.

tests/verify_dataset.py checks the generator's in-memory output. This checks
what actually made it through schema.sql, the SQL dump and the MySQL type
system -- which is not the same thing. A DECIMAL rounding difference or a
column comment that got truncated on load would pass there and fail here.

    .venv\\Scripts\\python -m pytest tests/test_defects_db.py -v
    .venv\\Scripts\\python tests/test_defects_db.py        # standalone
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from harness.db import DbConfig, connect  # noqa: E402

DEFECTS = yaml.safe_load((ROOT / "data" / "defects.yaml").read_text(encoding="utf-8"))["defects"]

# Minimum `n` each detect_sql must return for the defect to be measurable.
# A defect that fires on 2 rows cannot move an eval score.
MIN_EXPECTED = {
    "D1": 2,      # cust_id and acct_id both exist
    "D2": 5,      # combined status vocabulary
    "D3": 10,     # stale plan_tier rows
    "D4": 30,     # paid invoices with NULL paid_at
    "D5": 500,    # non-numeric event_value
    "D6": 5,      # duplicate company names
    "D7": 3000,   # batch-sourced (timezone-shifted) events
    "D8": 20,     # orphaned tickets
    "D9": 5,      # churn rows with undocumented reason codes
    "D10": 100,   # invoices with cents or unknown units
}


def _admin_cfg() -> DbConfig:
    """Use the admin account: some detect_sql read information_schema."""
    return DbConfig.admin()


def _run(sql: str) -> int:
    with connect(_admin_cfg()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone() or {}
    return int(row.get("n") or 0)


@pytest.mark.parametrize("defect", DEFECTS, ids=[d["id"] for d in DEFECTS])
def test_defect_present(defect):
    did = defect["id"]
    n = _run(defect["detect_sql"])
    floor = MIN_EXPECTED.get(did, 1)
    assert n >= floor, (
        f"{did} ({defect['title']}): detect_sql returned {n}, expected >= {floor}. "
        f"The defect is missing or too rare to measure."
    )


def test_read_only_user_cannot_write():
    """The agent's account must be unable to mutate. This is the only layer
    that actually enforces anything -- sqlglot parsing can be bypassed."""
    import pymysql
    with connect(DbConfig()) as conn, conn.cursor() as cur:
        with pytest.raises(pymysql.MySQLError):
            cur.execute("CREATE TABLE should_not_exist (id INT)")


def test_no_foreign_keys_declared():
    """D8 depends on there being no declared FKs. If someone 'helpfully' adds
    them, orphan rows become impossible and the defect silently disappears."""
    n = _run(
        "SELECT COUNT(*) AS n FROM information_schema.table_constraints "
        "WHERE table_schema = DATABASE() AND constraint_type = 'FOREIGN KEY'"
    )
    assert n == 0, f"{n} foreign keys declared; D8 cannot exist with FKs enforced"


def test_row_counts_match_generator():
    expected = {"employees": 40, "customers": 128, "subscriptions": 128,
                "invoices": 1902, "usage_events": 25_000, "support_tickets": 800,
                "campaigns": 12, "deals": 300, "product_catalog": 21,
                "churn_log": 49}
    for table, want in expected.items():
        got = _run(f"SELECT COUNT(*) AS n FROM `{table}`")
        assert got == want, f"{table}: loaded {got} rows, generator produced {want}"


def main() -> int:
    failures = 0
    print("Defect assertions against the loaded database:\n")
    for d in DEFECTS:
        did = d["id"]
        try:
            n = _run(d["detect_sql"])
        except Exception as e:
            print(f"  ERROR {did}: {e}")
            failures += 1
            continue
        floor = MIN_EXPECTED.get(did, 1)
        ok = n >= floor
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {did:<4} n={n:<7,} (need >={floor:<6,}) {d['title']}")

    print()
    for name, fn in [("no FKs declared", test_no_foreign_keys_declared),
                     ("row counts match generator", test_row_counts_match_generator)]:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}: {e}")

    print(f"\n{failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
