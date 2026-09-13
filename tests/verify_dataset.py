"""
Dataset self-check -- no database required.

Rebuilds the dataset in memory from data/generate.py and asserts that every
defect D1..D10 is actually present, at a usable frequency.

This exists because a defect that silently stops being injected does not break
anything loudly -- it just quietly makes the eval measure nothing. Run it in CI.

    py -3.11 tests/verify_dataset.py     # standalone, no pytest needed
    pytest tests/verify_dataset.py       # or as a normal test module

The DB-level equivalent (running each defect's `detect_sql` from defects.yaml
against a loaded MySQL) lives in tests/test_defects_db.py.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))

import generate as g  # noqa: E402


def build_all():
    """Mirror generate.generate()'s call order exactly -- the RNG stream is
    positional, so any reordering here produces different data than seed.sql."""
    rng = random.Random(g.SEED)
    _, emp = g.build_employees(rng)
    _, cus, customer_ids, ghost_ids = g.build_customers(rng)
    _, sub, tier_by_customer = g.build_subscriptions(rng, customer_ids)
    cus = g.apply_stale_plan_tier(rng, cus, tier_by_customer)
    _, inv = g.build_invoices(rng, sub)
    _, use = g.build_usage_events(rng, customer_ids)
    _, tic = g.build_support_tickets(rng, customer_ids, ghost_ids)
    _, cam = g.build_campaigns(rng)
    _, deal = g.build_deals(rng, customer_ids)
    _, cat = g.build_product_catalog(rng)
    _, chn = g.build_churn_log(rng, customer_ids, sub)
    return dict(employees=emp, customers=cus, subscriptions=sub, invoices=inv,
                usage_events=use, support_tickets=tic, campaigns=cam, deals=deal,
                product_catalog=cat, churn_log=chn,
                customer_ids=customer_ids, ghost_ids=ghost_ids)


D = build_all()

# Column positions, mirroring the `cols` lists in generate.py.
CUS = {"customer_id": 0, "company_name": 1, "plan_tier": 5, "account_manager_id": 6}
SUB = {"subscription_id": 0, "customer_id": 1, "tier": 2, "ended_on": 6, "status": 7}
INV = {"cust_id": 2, "amount": 3, "currency_minor": 4, "paid_at": 7, "status": 8}
USE = {"event_value": 3, "event_ts": 4, "source": 5}
TIC = {"cust_id": 1}
DEAL = {"acct_id": 1, "status": 5}
CAT = {"sku": 1, "effective_to": 6, "is_current": 7}
CHN = {"reason_code": 3, "recovered_on": 4}


def test_d1_inconsistent_key_naming():
    """The customer key is spelled three ways, and all three actually resolve."""
    valid = set(D["customer_ids"])
    assert {r[SUB["customer_id"]] for r in D["subscriptions"]} <= valid
    assert {r[INV["cust_id"]] for r in D["invoices"]} <= valid
    # deals.acct_id is the hard one: zero lexical overlap with 'customer_id'.
    assert {r[DEAL["acct_id"]] for r in D["deals"]} <= valid


def test_d2_status_vocabularies_are_disjoint():
    inv_vals = {r[INV["status"]] for r in D["invoices"]}
    deal_vals = {r[DEAL["status"]] for r in D["deals"]}
    sub_vals = {r[SUB["status"]] for r in D["subscriptions"]}
    assert inv_vals == {"paid", "unpaid", "void"}
    assert deal_vals == {"open", "won", "lost"}
    assert sub_vals <= {"active", "paused", "cancelled"}
    # The trap only exists if the vocabularies do not overlap at all.
    assert not (inv_vals & deal_vals)


def test_d3_stale_plan_tier():
    live = {r[SUB["customer_id"]]: r[SUB["tier"]]
            for r in D["subscriptions"] if r[SUB["ended_on"]] is None}
    stale = sum(1 for r in D["customers"]
                if r[CUS["plan_tier"]] is not None
                and r[CUS["customer_id"]] in live
                and r[CUS["plan_tier"]] != live[r[CUS["customer_id"]]])
    assert stale >= 10, f"only {stale} stale plan_tier rows -- too few to measure"


def test_d3_stale_is_current_flag():
    """is_current must contradict effective_to for at least one SKU."""
    bad = [r for r in D["product_catalog"]
           if bool(r[CAT["is_current"]]) != (r[CAT["effective_to"]] is None)]
    assert bad, "no rows where is_current disagrees with effective_to"


def test_d4_paid_invoices_with_null_timestamp():
    n = sum(1 for r in D["invoices"]
            if r[INV["paid_at"]] is None and r[INV["status"]] == "paid")
    assert n >= 30, f"only {n} paid-but-untimestamped invoices"


def test_d5_event_value_type_drift():
    numeric = re.compile(r"^[0-9]+(\.[0-9]+)?$")
    vals = [r[USE["event_value"]] for r in D["usage_events"]]
    junk = sum(1 for v in vals if v is not None and not numeric.match(v))
    nulls = sum(1 for v in vals if v is None)
    assert junk >= 500, f"only {junk} non-numeric event_value rows"
    assert nulls >= 100, f"only {nulls} NULL event_value rows"
    # The danger is that SUM() silently coerces junk to 0 instead of erroring.
    assert any(v == "N/A" for v in vals) and any(v == "" for v in vals)


def test_d6_duplicate_companies():
    norm = {}
    for r in D["customers"]:
        k = re.sub(r"[^a-z0-9]", "", r[CUS["company_name"]].lower())
        norm.setdefault(k, []).append(r[CUS["customer_id"]])
    dups = {k: v for k, v in norm.items() if len(v) > 1}
    assert len(dups) >= g.N_DUPLICATE_PAIRS - 2, f"expected ~{g.N_DUPLICATE_PAIRS} dup pairs, got {len(dups)}"


def test_d7_batch_rows_are_offset():
    batch = [r for r in D["usage_events"] if r[USE["source"]] == "batch"]
    assert len(batch) >= 3000, f"only {len(batch)} batch rows"
    # Shifted -4h means a slice of them fall before the nominal window start.
    assert any(r[USE["event_ts"]].date() < g.date(2025, 9, 1) for r in batch)


def test_d8_orphaned_ticket_keys():
    valid = set(D["customer_ids"])
    orphans = [r for r in D["support_tickets"] if r[TIC["cust_id"]] not in valid]
    assert len(orphans) >= 20, f"only {len(orphans)} orphaned tickets"
    # They must be a *minority*, or an INNER JOIN would look obviously broken
    # rather than subtly wrong -- which is the failure mode we want to catch.
    assert len(orphans) < len(D["support_tickets"]) * 0.15


def test_d9_undocumented_churn_codes():
    codes = [r[CHN["reason_code"]] for r in D["churn_log"]]
    undocumented = [c for c in codes if c > 4]
    assert undocumented, "no reason_code > 4 -- D9 is not being injected"
    assert max(codes) <= 9 and min(codes) >= 1


def test_d9_recovered_customers_are_not_churned():
    recovered = [r for r in D["churn_log"] if r[CHN["recovered_on"]] is not None]
    assert len(recovered) >= 10, f"only {len(recovered)} recovered rows"
    assert len(recovered) < len(D["churn_log"]), "everyone recovered -- no real churn left"


def test_d10_mixed_currency_units():
    minors = [r[INV["currency_minor"]] for r in D["invoices"]]
    assert minors.count(1) >= 100, "no cents-denominated invoices"
    assert minors.count(0) >= 100, "no dollar-denominated invoices"
    assert minors.count(None) >= 50, "no invoices with an unknown unit"


def test_d10_deal_amounts_are_tax_inclusive():
    """deals.amount is grossed up by 8%; invoices.amount is net. If the two
    ever became directly comparable the semantic trap would disappear."""
    amounts = [r[3] for r in D["deals"]]
    assert all(a > 0 for a in amounts)
    assert max(amounts) > 100_000


def test_row_counts_are_stable():
    expected = {"employees": 40, "customers": 128, "subscriptions": 128,
                "usage_events": 25_000, "support_tickets": 800,
                "campaigns": 12, "deals": 300}
    for table, n in expected.items():
        assert len(D[table]) == n, f"{table}: expected {n}, got {len(D[table])}"
    # These are derived, so assert a sane range rather than an exact number.
    assert 1_500 <= len(D["invoices"]) <= 2_500
    assert 30 <= len(D["churn_log"]) <= 80
    assert 15 <= len(D["product_catalog"]) <= 30


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
