"""
Deterministic generator for the synthetic B2B SaaS dataset.

Run:  python data/generate.py           -> writes data/seed.sql
      python data/generate.py --check   -> regenerates and diffs, exit 1 on drift

WHY THIS IS A SCRIPT AND NOT AN LLM CALL
----------------------------------------
The whole project rests on one comparison: same model, same questions, same
database, harness on vs harness off. That comparison is meaningless if the
database is not byte-identical across runs and across teammates' machines.
An LLM emitting rows cannot give us that. So a strong model authored the
schema, the defect spec and the gold questions -- and this seeded RNG emits
the rows.

DETERMINISM RULES (do not break these)
--------------------------------------
* One `random.Random(SEED)` instance, threaded through explicitly. Never use
  the module-level `random.*` functions -- they share global state.
* Never iterate a `set`. Sort first.
* No `datetime.now()`. `TODAY` is frozen below.
* Format every float/Decimal with an explicit format string.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
# Frozen parameters. Changing any of these changes the dataset, which
# invalidates every gold answer in questions.yaml. Bump DATASET_VERSION and
# re-verify the gold set if you touch them.
# --------------------------------------------------------------------------
DATASET_VERSION = "1.0.0"
SEED = 20260912

TODAY = date(2026, 9, 1)
EPOCH = date(2024, 1, 1)

N_CUSTOMERS = 120          # before duplicate injection
N_DUPLICATE_PAIRS = 8      # D6
N_EMPLOYEES = 40
N_CAMPAIGNS = 12
N_DEALS = 300
N_TICKETS = 800
N_USAGE_EVENTS = 25_000

# ID-space bases. Each entity gets a DISJOINT id range, as it would if the
# tables came from separate services with their own sequences.
#
# This is not cosmetic. With every table numbered 1..N, small integer ranges
# trivially contain one another, and value-overlap join inference cannot tell
# customers.customer_id from subscriptions.subscription_id -- they are literally
# the same set. That is an artefact of lazy synthetic data, not a real-world
# difficulty, and it was drowning harness/joins.py in false ambiguity.
#
# Employees stay at 1..40: several columns legitimately reference them, and
# keeping one small dense key space preserves a realistic inference challenge.
CUSTOMER_ID_BASE = 1_000
GHOST_ID_BASE = 1_900      # referenced by tickets, absent from customers (D8)
SUBSCRIPTION_ID_BASE = 5_000
CAMPAIGN_ID_BASE = 7_000
CHURN_ID_BASE = 8_000

# Billing system cutover. Invoices before this date came from the legacy
# biller, which stored minor units (cents). After, dollars. (D10)
BILLER_CUTOVER = date(2025, 7, 1)

TIERS = ["free", "starter", "growth", "enterprise"]
TIER_MRR_CENTS = {"free": 0, "starter": 4900, "growth": 29900, "enterprise": 149900}

OUT_PATH = Path(__file__).with_name("seed.sql")

# --------------------------------------------------------------------------
# Name material. Fixed lists -> reproducible company/person names.
# --------------------------------------------------------------------------
_CO_A = ["North", "Blue", "Iron", "Cedar", "Vertex", "Lumen", "Harbor", "Quartz",
         "Solstice", "Meridian", "Copper", "Falcon", "Aster", "Onyx", "Clearwater",
         "Redwood", "Summit", "Tidal", "Ember", "Granite"]
_CO_B = ["wind", "peak", "gate", "works", "field", "bridge", "stone", "wave",
         "grove", "point", "ridge", "line", "spire", "haven"]
_CO_C = ["Systems", "Logistics", "Analytics", "Labs", "Group", "Partners",
         "Industries", "Digital", "Holdings", "Solutions"]

_FIRST = ["Ana", "Ben", "Chen", "Dara", "Elena", "Farid", "Grace", "Hugo", "Iris",
          "Jonas", "Kira", "Luis", "Maya", "Noor", "Omar", "Priya", "Quinn",
          "Rosa", "Sam", "Tomas", "Uma", "Vera", "Wes", "Yara", "Zane"]
_LAST = ["Alvarez", "Brenner", "Cho", "Duarte", "Eriksen", "Foster", "Gupta",
         "Haddad", "Ito", "Jensen", "Kowalski", "Lindqvist", "Moreau", "Nakamura",
         "Okafor", "Petrov", "Ramirez", "Silva", "Tanaka", "Varga"]

INDUSTRIES = ["Retail", "Healthcare", "Logistics", "Fintech", "Manufacturing",
              "Education", "Media", "Energy"]
COUNTRIES = ["United States", "Canada", "United Kingdom", "Germany", "Japan",
             "Australia", "Brazil", "India"]
TEAMS = ["Support", "Sales", "Customer Success", "Engineering", "Marketing"]
FEATURES = ["export.csv", "report.build", "api.query", "dashboard.view",
            "alert.create", "seat.invite", "webhook.deliver", "search.run"]


# ==========================================================================
# SQL emission helpers
# ==========================================================================

def esc(v) -> str:
    """Render a Python value as a MySQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, datetime):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(v, date):
        return "'" + v.strftime("%Y-%m-%d") + "'"
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + s + "'"


def insert_stmts(table: str, cols: list[str], rows: list[tuple], batch: int = 500) -> list[str]:
    """Multi-row INSERTs. Batched so a 25k-row table does not become one
    statement too large for the default max_allowed_packet."""
    if not rows:
        return []
    out = []
    collist = ", ".join(f"`{c}`" for c in cols)
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ",\n".join("  (" + ", ".join(esc(v) for v in r) + ")" for r in chunk)
        out.append(f"INSERT INTO `{table}` ({collist}) VALUES\n{values};")
    return out


def rand_date(rng: random.Random, start: date, end: date) -> date:
    span = (end - start).days
    return start + timedelta(days=rng.randint(0, max(span, 0)))


def rand_dt(rng: random.Random, start: date, end: date) -> datetime:
    d = rand_date(rng, start, end)
    return datetime(d.year, d.month, d.day,
                    rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))


# ==========================================================================
# Table builders. Each returns (cols, rows) plus whatever later tables need.
# ==========================================================================

def build_employees(rng):
    cols = ["employee_id", "full_name", "team", "hired_on", "left_on", "manager_id"]
    rows = []
    for eid in range(1, N_EMPLOYEES + 1):
        name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        hired = rand_date(rng, date(2021, 1, 1), date(2026, 3, 1))
        # ~15% have left. They still own accounts in customers -- that is the
        # point: "who owns this account" has no good answer for those rows.
        left = rand_date(rng, hired + timedelta(days=200), TODAY) if rng.random() < 0.15 else None
        mgr = rng.randint(1, 6) if eid > 6 else None
        rows.append((eid, name, rng.choice(TEAMS), hired, left, mgr))
    return cols, rows


def build_customers(rng):
    """Returns (cols, rows, customer_ids, tier_by_customer, ghost_ids).

    ghost_ids are customer_ids referenced by support_tickets but deliberately
    absent from this table (D8).
    """
    cols = ["customer_id", "company_name", "industry", "country", "signup_date",
            "plan_tier", "account_manager_id", "is_active", "created_at"]
    rows = []
    names_used = []

    # Base companies -----------------------------------------------------
    for cid in range(CUSTOMER_ID_BASE + 1, CUSTOMER_ID_BASE + N_CUSTOMERS + 1):
        if rng.random() < 0.5:
            nm = f"{rng.choice(_CO_A)}{rng.choice(_CO_B)} {rng.choice(_CO_C)}"
        else:
            nm = f"{rng.choice(_CO_A)} {rng.choice(_CO_C)}"
        names_used.append((cid, nm))
        signup = rand_date(rng, EPOCH, date(2026, 6, 1))
        rows.append((
            cid, nm, rng.choice(INDUSTRIES), rng.choice(COUNTRIES), signup,
            None,                                   # plan_tier filled in later (D3)
            rng.randint(1, N_EMPLOYEES),            # D1: account_manager_id
            1 if rng.random() < 0.88 else 0,
            datetime(signup.year, signup.month, signup.day,
                     rng.randint(8, 18), rng.randint(0, 59), 0),
        ))

    # D6: duplicate entities -- same company, second surrogate key, name variant.
    suffixes = [", Inc.", " Inc", " Incorporated", " LLC", " Ltd."]
    next_id = CUSTOMER_ID_BASE + N_CUSTOMERS + 1
    dup_sources = rng.sample(range(len(names_used)), N_DUPLICATE_PAIRS)
    for idx in sorted(dup_sources):
        orig_cid, orig_nm = names_used[idx]
        variant = orig_nm + rng.choice(suffixes)
        signup = rand_date(rng, EPOCH, date(2026, 6, 1))
        rows.append((
            next_id, variant, rng.choice(INDUSTRIES), rng.choice(COUNTRIES), signup,
            None, rng.randint(1, N_EMPLOYEES), 1,
            datetime(signup.year, signup.month, signup.day, 12, 0, 0),
        ))
        next_id += 1

    customer_ids = [r[0] for r in rows]
    # D8: ids that tickets will reference but which are NOT in customers.
    # Deliberately adjacent to the real customer range -- a plausible-looking
    # key is a better trap than an obviously alien one.
    ghost_ids = list(range(GHOST_ID_BASE + 1, GHOST_ID_BASE + 8))
    return cols, rows, customer_ids, ghost_ids


def build_subscriptions(rng, customer_ids):
    cols = ["subscription_id", "customer_id", "tier", "seats", "mrr_cents",
            "started_on", "ended_on", "status", "updated_at"]
    rows = []
    tier_by_customer = {}
    sid = SUBSCRIPTION_ID_BASE + 1
    for cid in customer_ids:
        tier = rng.choices(TIERS, weights=[15, 35, 35, 15])[0]
        started = rand_date(rng, EPOCH, date(2026, 5, 1))
        roll = rng.random()
        if roll < 0.12:
            status, ended = "cancelled", rand_date(rng, started + timedelta(days=60), TODAY)
        elif roll < 0.18:
            status, ended = "paused", None
        else:
            status, ended = "active", None
        seats = max(1, int(rng.gauss(25, 18)))
        rows.append((sid, cid, tier, seats, TIER_MRR_CENTS[tier], started, ended,
                     status, rand_dt(rng, date(2026, 6, 1), TODAY)))
        tier_by_customer[cid] = (tier, ended)
        sid += 1
    return cols, rows, tier_by_customer


def apply_stale_plan_tier(rng, customer_rows, tier_by_customer):
    """D3: customers.plan_tier is a cached copy of subscriptions.tier that the
    nightly job stopped refreshing. ~18% are stale (usually one tier behind)."""
    out = []
    for r in customer_rows:
        r = list(r)
        cid = r[0]
        true_tier, _ = tier_by_customer.get(cid, (None, None))
        if true_tier is None:
            r[5] = None
        elif rng.random() < 0.18:
            others = [t for t in TIERS if t != true_tier]
            r[5] = rng.choice(others)          # stale: disagrees with truth
        elif rng.random() < 0.04:
            r[5] = None                        # never populated at all
        else:
            r[5] = true_tier
        out.append(tuple(r))
    return out


def build_invoices(rng, subscription_rows):
    """D1 (cust_id), D2 (status vocabulary), D4 (ambiguous paid_at NULL),
    D10 (cents vs dollars, tax-exclusive)."""
    cols = ["invoice_id", "subscription_id", "cust_id", "amount",
            "currency_minor", "tax_included", "issued_at", "paid_at", "status"]
    rows = []
    inv_id = 1
    for sub in subscription_rows:
        sid, cid, tier, seats, mrr_cents, started, ended, status, _ = sub
        if mrr_cents == 0:
            continue                                   # free tier is not billed
        cursor = date(started.year, started.month, 1)
        stop = ended or TODAY
        while cursor < stop:
            dollars = round(mrr_cents / 100.0 * max(1, seats // 10), 2)

            # D10: which biller issued it decides the unit.
            if cursor < BILLER_CUTOVER:
                amount, minor = round(dollars * 100, 2), 1
            else:
                amount, minor = dollars, 0
            # ...and 15% of rows lost the unit flag entirely.
            if rng.random() < 0.15:
                minor = None

            # D2: invoices use paid/unpaid/void -- NOT deals' open/won/lost.
            roll = rng.random()
            inv_status = "paid" if roll < 0.82 else ("unpaid" if roll < 0.96 else "void")

            issued = datetime(cursor.year, cursor.month, min(28, rng.randint(1, 28)),
                              rng.randint(0, 23), rng.randint(0, 59), 0)
            if inv_status == "paid":
                # D4: 9% of PAID invoices never got their timestamp backfilled,
                # so `paid_at IS NULL` does NOT mean unpaid.
                paid = None if rng.random() < 0.09 else issued + timedelta(
                    days=rng.randint(1, 35), hours=rng.randint(0, 23))
            else:
                paid = None

            rows.append((inv_id, sid, cid, amount, minor, 0, issued, paid, inv_status))
            inv_id += 1
            cursor = date(cursor.year + (cursor.month // 12), (cursor.month % 12) + 1, 1)
    return cols, rows


def build_usage_events(rng, customer_ids):
    """D5 (numeric-as-text with junk) and D7 (batch rows are US/Eastern)."""
    cols = ["event_id", "customer_id", "feature_key", "event_value", "event_ts", "source"]
    rows = []
    for eid in range(1, N_USAGE_EVENTS + 1):
        cid = rng.choice(customer_ids)
        src = rng.choices(["api", "web", "batch"], weights=[50, 30, 20])[0]
        ts = rand_dt(rng, date(2025, 9, 1), TODAY)
        # D7: batch rows were written in US/Eastern (UTC-4), everything else UTC.
        # Stored naive, with no tz column to tell you which is which.
        if src == "batch":
            ts = ts - timedelta(hours=4)

        # D5: declared VARCHAR, and the writer was sloppy about it.
        roll = rng.random()
        if roll < 0.04:
            val = "N/A"
        elif roll < 0.06:
            val = ""
        elif roll < 0.07:
            val = None
        elif roll < 0.40:
            val = f"{rng.uniform(0.5, 400):.1f}"       # '12.0' style
        else:
            val = str(rng.randint(1, 400))            # '12' style
        rows.append((eid, cid, rng.choice(FEATURES), val, ts, src))
    return cols, rows


def build_support_tickets(rng, customer_ids, ghost_ids):
    """D1 (cust_id) and D8 (orphans pointing at hard-deleted customers)."""
    cols = ["ticket_id", "cust_id", "assigned_to", "opened_at", "resolved_at",
            "priority", "csat", "subject"]
    subjects = ["Export fails on large report", "SSO login loop", "Billing amount mismatch",
                "API 429 on burst", "Webhook not firing", "Seat invite bounced",
                "Dashboard blank after upgrade", "Data missing for last week"]
    rows = []
    for tid in range(1, N_TICKETS + 1):
        # D8: ~5% reference customers that were hard-deleted for GDPR without
        # cascading. INNER JOIN silently drops these.
        cid = rng.choice(ghost_ids) if rng.random() < 0.05 else rng.choice(customer_ids)
        opened = rand_dt(rng, date(2025, 9, 1), TODAY)
        resolved = opened + timedelta(hours=rng.randint(1, 340)) if rng.random() < 0.8 else None
        csat = rng.randint(1, 5) if (resolved and rng.random() < 0.55) else None
        rows.append((tid, cid, rng.randint(1, N_EMPLOYEES), opened, resolved,
                     rng.choice(["P1", "P2", "P3", "P4"]), csat, rng.choice(subjects)))
    return cols, rows


def build_campaigns(rng):
    cols = ["campaign_id", "name", "channel", "budget_usd", "start_date", "end_date"]
    channels = ["email", "paid_search", "conference", "webinar", "partner"]
    rows = []
    for kid in range(CAMPAIGN_ID_BASE + 1, CAMPAIGN_ID_BASE + N_CAMPAIGNS + 1):
        start = rand_date(rng, EPOCH, date(2026, 5, 1))
        rows.append((kid, f"{rng.choice(['Q1','Q2','Q3','Q4'])} {rng.choice(_CO_A)} Push",
                     rng.choice(channels), round(rng.uniform(5_000, 90_000), 2),
                     start, start + timedelta(days=rng.randint(30, 120))))
    return cols, rows


def build_deals(rng, customer_ids):
    """D1 (acct_id -- no lexical overlap with customer_id), D2 (status
    vocabulary), D10 (tax-INCLUSIVE dollars)."""
    cols = ["deal_id", "acct_id", "owner_id", "amount", "stage", "status",
            "campaign_id", "close_dt"]
    stages = ["discovery", "demo", "proposal", "negotiation", "closed"]
    rows = []
    for did in range(1, N_DEALS + 1):
        roll = rng.random()
        # D2: open/won/lost -- a different vocabulary from invoices.status.
        status = "won" if roll < 0.34 else ("lost" if roll < 0.58 else "open")
        close = rand_date(rng, EPOCH, TODAY) if status != "open" else None
        # D10: tax-inclusive, always dollars. Comparing to invoices.amount
        # (tax-exclusive, sometimes cents) without normalising is the trap.
        base = rng.uniform(2_000, 180_000)
        rows.append((did, rng.choice(customer_ids), rng.randint(1, N_EMPLOYEES),
                     round(base * 1.08, 2),
                     "closed" if status != "open" else rng.choice(stages[:4]),
                     status,
                     CAMPAIGN_ID_BASE + rng.randint(1, N_CAMPAIGNS) if rng.random() < 0.7 else None,
                     close))
    return cols, rows


def build_product_catalog(rng):
    """SCD-2 price list. Joining on sku alone fans out rows and picks up
    superseded prices. D3: is_current disagrees with effective_to for 2 SKUs."""
    cols = ["catalog_id", "sku", "product_name", "tier", "list_price_usd",
            "effective_from", "effective_to", "is_current"]
    skus = [("SKU-FREE", "Community", "free", 0.0),
            ("SKU-STR", "Starter", "starter", 49.0),
            ("SKU-GRW", "Growth", "growth", 299.0),
            ("SKU-ENT", "Enterprise", "enterprise", 1499.0),
            ("SKU-ADD-SSO", "SSO Add-on", "enterprise", 199.0),
            ("SKU-ADD-API", "API Add-on", "growth", 99.0)]
    rows = []
    cat_id = 1
    broken = {"SKU-GRW", "SKU-ADD-API"}          # D3: stale is_current flag
    for sku, pname, tier, base in skus:
        n_versions = rng.randint(2, 4)
        start = EPOCH
        for v in range(n_versions):
            last = (v == n_versions - 1)
            end = None if last else start + timedelta(days=rng.randint(180, 400))
            price = round(base * (1.0 + 0.07 * v), 2)
            if sku in broken:
                # Botched backfill: flag says current on a superseded row, and
                # the genuinely-current row is flagged stale. effective_to wins.
                is_cur = 1 if v == 0 else 0
            else:
                is_cur = 1 if last else 0
            rows.append((cat_id, sku, pname, tier, price, start, end, is_cur))
            cat_id += 1
            if end:
                start = end
    return cols, rows


def build_churn_log(rng, customer_ids, subscription_rows):
    """D9: reason_code 1-9, but only 1-4 are documented in the column COMMENT.

    This is an append-only HISTORICAL log, not a mirror of current state. It
    therefore contains two kinds of row:

      * customers whose subscription is currently 'cancelled'  -- still churned
      * customers who churned earlier and later resubscribed   -- recovered_on
        is set and they have an ACTIVE subscription today

    That second group is the trap: `SELECT COUNT(*) FROM churn_log` is not the
    churn count, and joining churn_log to subscriptions without filtering
    recovered_on double-counts accounts that came back.
    """
    cols = ["churn_id", "customer_id", "churned_on", "reason_code", "recovered_on"]

    cancelled = sorted({s[1] for s in subscription_rows if s[7] == "cancelled"})
    # Historical churners who have since returned. Drawn from customers that are
    # NOT currently cancelled, so their subscription row reads 'active'.
    eligible = sorted(set(customer_ids) - set(cancelled))
    n_returned = max(30, len(eligible) // 3)
    returned = rng.sample(eligible, min(n_returned, len(eligible)))

    rows = []
    churn_id = CHURN_ID_BASE + 1

    for cid in cancelled:
        churned = rand_date(rng, date(2024, 6, 1), TODAY)
        # 1-4 documented in the COMMENT, 5-9 are not. ~35% undocumented.
        code = rng.randint(1, 4) if rng.random() < 0.65 else rng.randint(5, 9)
        rows.append((churn_id, cid, churned, code, None))
        churn_id += 1

    for cid in sorted(returned):
        churned = rand_date(rng, date(2024, 6, 1), date(2026, 2, 1))
        code = rng.randint(1, 4) if rng.random() < 0.65 else rng.randint(5, 9)
        recovered = rand_date(rng, churned + timedelta(days=30), TODAY)
        rows.append((churn_id, cid, churned, code, recovered))
        churn_id += 1

    return cols, rows


# ==========================================================================
# Assembly
# ==========================================================================

def generate() -> str:
    rng = random.Random(SEED)

    emp_cols, emp_rows = build_employees(rng)
    cus_cols, cus_rows, customer_ids, ghost_ids = build_customers(rng)
    sub_cols, sub_rows, tier_by_customer = build_subscriptions(rng, customer_ids)
    cus_rows = apply_stale_plan_tier(rng, cus_rows, tier_by_customer)   # D3
    inv_cols, inv_rows = build_invoices(rng, sub_rows)
    use_cols, use_rows = build_usage_events(rng, customer_ids)
    tic_cols, tic_rows = build_support_tickets(rng, customer_ids, ghost_ids)
    cam_cols, cam_rows = build_campaigns(rng)
    deal_cols, deal_rows = build_deals(rng, customer_ids)
    cat_cols, cat_rows = build_product_catalog(rng)
    chn_cols, chn_rows = build_churn_log(rng, customer_ids, sub_rows)

    schema_sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")

    parts: list[str] = [
        "-- GENERATED FILE -- do not edit by hand.",
        f"-- Produced by data/generate.py  (version {DATASET_VERSION}, seed {SEED}).",
        "-- Regenerate with:  python data/generate.py",
        "--",
        "-- The anomalies in this data are INTENTIONAL. See data/defects.yaml.",
        "",
        schema_sql,
        "",
        "SET autocommit = 0;",
        "START TRANSACTION;",
        "",
    ]

    for label, table, cols, rows in [
        ("employees", "employees", emp_cols, emp_rows),
        ("customers", "customers", cus_cols, cus_rows),
        ("subscriptions", "subscriptions", sub_cols, sub_rows),
        ("invoices", "invoices", inv_cols, inv_rows),
        ("usage_events", "usage_events", use_cols, use_rows),
        ("support_tickets", "support_tickets", tic_cols, tic_rows),
        ("campaigns", "campaigns", cam_cols, cam_rows),
        ("deals", "deals", deal_cols, deal_rows),
        ("product_catalog", "product_catalog", cat_cols, cat_rows),
        ("churn_log", "churn_log", chn_cols, chn_rows),
    ]:
        parts.append(f"-- {label}: {len(rows)} rows")
        parts.extend(insert_stmts(table, cols, rows))
        parts.append("")

    parts += ["COMMIT;", "SET autocommit = 1;", ""]
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="Regenerate and fail if it differs from the committed seed.sql.")
    args = ap.parse_args()

    sql = generate()

    if args.check:
        if not OUT_PATH.exists():
            print("FAIL: seed.sql does not exist; run without --check first.", file=sys.stderr)
            return 1
        current = OUT_PATH.read_text(encoding="utf-8")
        if current != sql:
            print("FAIL: generator output drifted from committed seed.sql.\n"
                  "The A/B comparison is only valid if this is reproducible.\n"
                  "Either re-run `python data/generate.py` and commit, or find\n"
                  "the nondeterminism (unsorted set? module-level random? now()?).",
                  file=sys.stderr)
            return 1
        print(f"OK: seed.sql is reproducible ({len(sql):,} bytes).")
        return 0

    OUT_PATH.write_text(sql, encoding="utf-8", newline="\n")
    print(f"Wrote {OUT_PATH} ({len(sql):,} bytes, version {DATASET_VERSION}, seed {SEED}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
