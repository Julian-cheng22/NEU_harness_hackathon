"""
Tests for the harness tools against the loaded database.

These assert the BEHAVIOUR the eval depends on. If join inference stops finding
deals.acct_id, or the profiler stops warning about event_value, the harness arm
of the eval quietly degrades to the baseline arm and the whole comparison
becomes meaningless without anything visibly failing. Hence these tests.

    .venv\\Scripts\\python -m pytest tests/test_harness_tools.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from harness import joins, profile, schema_card  # noqa: E402
from harness.db import SqlRefused, assert_read_only, run_query  # noqa: E402


@pytest.fixture(scope="module")
def candidates():
    return joins.infer_joins()


@pytest.fixture(scope="module")
def by_child(candidates):
    return {c.child: c for c in candidates}


# ---------------------------------------------------------------------------
# Join inference
# ---------------------------------------------------------------------------

# Every relationship the eval questions actually traverse.
EXPECTED_JOINS = {
    "subscriptions.customer_id": "customers.customer_id",
    "invoices.cust_id": "customers.customer_id",          # D1
    "support_tickets.cust_id": "customers.customer_id",   # D1 + D8
    "deals.acct_id": "customers.customer_id",             # D1, zero name overlap
    "usage_events.customer_id": "customers.customer_id",
    "churn_log.customer_id": "customers.customer_id",
    "invoices.subscription_id": "subscriptions.subscription_id",
    "deals.campaign_id": "campaigns.campaign_id",
    "deals.owner_id": "employees.employee_id",
    "customers.account_manager_id": "employees.employee_id",
    "support_tickets.assigned_to": "employees.employee_id",
}


@pytest.mark.parametrize("child,parent", sorted(EXPECTED_JOINS.items()))
def test_join_inferred(by_child, child, parent):
    assert child in by_child, f"{child} was not inferred at all"
    assert by_child[child].parent == parent, (
        f"{child} resolved to {by_child[child].parent}, expected {parent}"
    )


def test_acct_id_found_without_name_overlap(by_child):
    """The flagship case: 'acct_id' shares no substring with 'customer_id',
    so no amount of lexical matching finds it. Only value overlap does."""
    jc = by_child["deals.acct_id"]
    assert jc.parent == "customers.customer_id"
    assert jc.containment == 1.0
    assert not jc.is_ambiguous


def test_orphaned_tickets_are_surfaced(by_child):
    """D8. This is the whole point: an INNER JOIN would hide these, and an
    inference that reports the relationship as 'clean' hides them too."""
    jc = by_child["support_tickets.cust_id"]
    assert not jc.is_clean, "orphaned ticket keys were not detected"
    assert jc.orphan_values == 7, f"expected 7 orphan keys, got {jc.orphan_values}"
    assert "LEFT JOIN" in jc.describe()


def test_orphan_report_distinguishes_rows_from_distinct_values(by_child):
    """Regression, measured in the first full eval run.

    This line used to report only the DISTINCT-VALUE count (7). Asked "how many
    support tickets reference a customer that no longer exists?" -- whose answer
    is 48 ROWS -- the model read 7 off the tool output and reported it. The
    harness handed it the wrong number in the right-sounding words, and D8 went
    from 2/2 on the baseline to 0/2 with the harness.
    """
    jc = by_child["support_tickets.cust_id"]
    assert jc.orphan_rows == 48, f"expected 48 orphan rows, got {jc.orphan_rows}"
    assert jc.orphan_values == 7
    assert jc.orphan_rows != jc.orphan_values, "the whole point is that these differ"

    text = jc.describe()
    assert "48 ROW(S)" in text
    assert "7 distinct" in text
    # The output must say which number answers a "how many rows" question.
    assert "the answer is 48" in text.lower()


def test_orphan_summary_is_not_phrased_as_an_instruction(candidates):
    """Regression: the summary line used to end with an imperative ('Prefer
    LEFT JOIN...'), and a 9B obeyed it instead of answering the question --
    returning an orphan count for a question about deal value by industry."""
    text = joins.format_for_model(candidates)
    assert "REFERENTIAL INTEGRITY IS BROKEN" not in text
    assert "Relevant only if your query joins on them." in text


def test_no_spurious_measure_columns(by_child):
    """Regression: naive containment matched `subscriptions.seats` to
    customers.customer_id purely because seat counts fell in 1..128."""
    for bad in ("subscriptions.seats", "support_tickets.csat",
                "churn_log.reason_code", "invoices.currency_minor"):
        assert bad not in by_child, f"{bad} is a measure, not a foreign key"


def test_ticket_key_does_not_resolve_to_event_id(by_child):
    """Regression for the worst failure mode observed: cust_id matched
    usage_events.event_id at 100% containment and reported it CLEAN, which
    hid D8 entirely."""
    assert by_child["support_tickets.cust_id"].parent != "usage_events.event_id"


def test_ambiguity_is_reported_not_guessed(candidates):
    """Where the data genuinely cannot separate two parents, the tool must say
    so. Silently picking one is the failure mode we are guarding against."""
    for c in candidates:
        if c.is_ambiguous:
            assert c.alternatives
            assert "AMBIGUOUS" in c.describe()


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------

def _warnings(table: str, column: str) -> str:
    return " ".join(profile.profile_column(table, column).warnings)


def test_d5_type_drift_warning():
    w = _warnings("usage_events", "event_value")
    assert "TYPE DRIFT" in w
    assert "coerces" in w.lower() and "without raising an error" in w.lower()
    # Must name the operations that ACTUALLY break. An earlier version of this
    # warning claimed SUM() understates the total, which is false -- junk
    # coerces to 0 and zero adds nothing. Naming the wrong culprit is worse
    # than saying nothing, because the model then "fixes" a working query.
    assert "MAX" in w and "AVG" in w
    assert "LEXICALLY" in w.upper()
    assert "SUM() happens to be safe" in w


def test_d4_ambiguous_null_warning():
    w = _warnings("invoices", "paid_at")
    assert "AMBIGUOUS NULL" in w
    assert "status" in w                   # must point at the disambiguator


def test_d3_stale_cache_warning():
    w = _warnings("customers", "plan_tier")
    assert "STALE CACHE" in w
    assert "subscriptions" in w


def test_d3_stale_flag_warning():
    w = _warnings("product_catalog", "is_current")
    assert "STALE FLAG" in w
    assert "effective_to" in w


def test_d9_undocumented_enum_warning():
    p = profile.profile_column("churn_log", "reason_code")
    w = " ".join(p.warnings)
    assert "UNDOCUMENTED VALUES" in w
    # The full domain must be enumerated, or the model cannot see 5..9 exist.
    assert "full domain" in p.stats
    for code in ("5", "6", "7", "8", "9"):
        assert code in p.stats["full domain"]


def test_d6_duplicate_entity_warning():
    w = _warnings("customers", "company_name")
    assert "DUPLICATE ENTITIES" in w


def test_profile_rejects_unknown_column():
    with pytest.raises(ValueError):
        profile.profile_column("customers", "no_such_column")


# ---------------------------------------------------------------------------
# Read-only execution guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE churn_log",
    "DELETE FROM customers",
    "UPDATE customers SET plan_tier = 'free'",
    "INSERT INTO customers (customer_id) VALUES (1)",
    "SELECT 1; SELECT 2",
    "TRUNCATE TABLE deals",
])
def test_mutations_refused_before_execution(sql):
    with pytest.raises(SqlRefused):
        assert_read_only(sql)


def test_select_allowed():
    r = run_query("SELECT COUNT(*) AS n FROM customers")
    assert r["ok"] and r["rows"][0]["n"] == 128


def test_row_cap_applied():
    """Without a cap, one `SELECT *` floods the model's context and the agent
    loop dies on a context-length error."""
    r = run_query("SELECT * FROM usage_events")
    assert r["ok"] and r["truncated"]
    assert "LIMIT" in r["sql"].upper()
    assert r["row_count"] <= 200


def test_sql_error_is_returned_not_raised():
    """The repair loop needs the MySQL message as data, not as an exception."""
    r = run_query("SELECT nope FROM customers")
    assert not r["ok"]
    assert "1054" in r["error"] and "nope" in r["error"]


# ---------------------------------------------------------------------------
# Schema card
# ---------------------------------------------------------------------------

def test_m_schema_enumerates_status_domains(candidates):
    """D2: the model can only tell invoices.status from deals.status if it can
    see both value domains."""
    card = schema_card.render(inferred_joins=joins.as_pairs(candidates))
    for v in ("'paid'", "'unpaid'", "'void'", "'won'", "'lost'", "'open'"):
        assert v in card, f"{v} missing from schema card"


def test_m_schema_includes_inferred_join_block(candidates):
    card = schema_card.render(inferred_joins=joins.as_pairs(candidates))
    assert "[Foreign keys]" in card
    assert "deals.acct_id = customers.customer_id" in card
    assert "INFERRED" in card


def test_raw_ddl_baseline_lacks_value_domains():
    """The baseline arm must be genuinely weaker, or the comparison is rigged
    in the other direction. Raw DDL should NOT reveal the status vocabularies."""
    ddl = schema_card.render_raw_ddl()
    assert "CREATE TABLE" in ddl
    assert "'won'" not in ddl and "'paid'" not in ddl
    assert "FOREIGN KEY" not in ddl.upper()
