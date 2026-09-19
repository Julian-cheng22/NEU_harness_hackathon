"""Deterministic generator for the synthetic e-commerce order database.

    python ecommerce/generate.py           -> writes ecommerce/seed.sql
    python ecommerce/generate.py --check   -> regenerates and diffs (exit 1 on drift)

All customers and products are fictional. Bump DATASET_VERSION whenever parameters
or logic change: gold answers in questions.yaml must be re-verified.

Time model. Data window is 2024-11-04 .. 2025-03-08, entirely inside US Eastern
*standard* time (UTC-5): DST ended 2024-11-03 and resumes 2025-03-09.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DATASET_VERSION = "1.0.0"
SEED = 20260919

# ---- frozen parameters ---------------------------------------------------
N_BASE_CUSTOMERS = 200
N_DUP_CUSTOMERS = 8           # D6
N_PRODUCTS = 150
N_ORDERS = 900
WINDOW_START = date(2024, 11, 4)
WINDOW_END = date(2025, 3, 8)
WINDOW_END_DT = datetime(2025, 3, 8, 23, 59)

P_STALE_CUSTOMER = 0.18       # D3
P_STALE_PRODUCT = 0.25        # D3
P_LOST = 0.02                 # D4 (delivered_at NULL although shipment is closed)
P_CANCELLED = 0.05
P_LEGACY_REASON = 0.12        # D9 (codes 5-9)
P_RATING_JUNK = 0.04          # D5
P_WEIGHT_JUNK = 0.08          # D5
P_LEGACY_GATEWAY = 0.30       # D10: LEGACY gateway stores cents
P_WEB = 0.6                   # D7: WEB rows are stored in UTC
P_ORPHAN_ORDER = 0.05         # D8
P_RETURN = 0.09
P_REVIEW = 0.30
UTC_OFFSET = timedelta(hours=5)

CATEGORIES = [(10, "Electronics"), (11, "Home"), (12, "Clothing"), (13, "Beauty"),
              (14, "Sports"), (15, "Toys"), (16, "Books"), (17, "Grocery")]
WAREHOUSES = [(50, "Reno Fulfillment", "NV"), (51, "Dallas Fulfillment", "TX"),
              (52, "Columbus Fulfillment", "OH"), (53, "Newark Fulfillment", "NJ"),
              (54, "Atlanta Fulfillment", "GA")]
FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
         "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
         "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Nancy", "Matthew", "Lisa"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas",
        "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris"]
STATES = ["CA", "NY", "TX", "FL", "IL", "WA", "MA", "OH", "GA", "NJ"]
ADJ = ["Compact", "Deluxe", "Eco", "Smart", "Classic", "Pro", "Mini", "Ultra", "Travel", "Premium"]
NOUN = {10: ["Speaker", "Charger", "Headphones", "Webcam", "Keyboard"],
        11: ["Lamp", "Blender", "Cushion", "Rug", "Kettle"],
        12: ["Jacket", "Sneakers", "Scarf", "Hoodie", "Gloves"],
        13: ["Moisturizer", "Shampoo", "Serum", "Lipstick", "Sunscreen"],
        14: ["Yoga Mat", "Dumbbell", "Water Bottle", "Backpack", "Jump Rope"],
        15: ["Puzzle", "Robot Kit", "Board Game", "Plush", "Blocks"],
        16: ["Cookbook", "Novel", "Atlas", "Journal", "Textbook"],
        17: ["Coffee Beans", "Olive Oil", "Granola", "Tea Set", "Honey"]}
CARRIERS = ["UPS", "FedEx", "USPS"]
WEIGHT_JUNK = ["N/A", "", "varies", "TBD"]


# ---- helpers ---------------------------------------------------------------
def esc(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, datetime):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(v, date):
        return "'" + v.isoformat() + "'"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return ("%.3f" % v).rstrip("0").rstrip(".")
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + s + "'"


def insert_stmts(table: str, cols: list[str], rows: list[tuple], batch: int = 500) -> list[str]:
    """Multi-row INSERTs, batched to stay below max_allowed_packet."""
    if not rows:
        return []
    out = []
    collist = ", ".join(f"`{c}`" for c in cols)
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ",\n".join("  (" + ", ".join(esc(v) for v in r) + ")" for r in chunk)
        out.append(f"INSERT INTO `{table}` ({collist}) VALUES\n{values};")
    return out


def rand_dt(rng: random.Random, lo: datetime, hi: datetime) -> datetime:
    span = int((hi - lo).total_seconds())
    return (lo + timedelta(seconds=rng.randint(0, max(span, 0)))).replace(microsecond=0)


# ---- table builders --------------------------------------------------------
def build_products(rng):
    rows, pid = [], 1000
    for _ in range(N_PRODUCTS):
        cat = rng.choice(CATEGORIES)[0]
        name = f"{rng.choice(ADJ)} {rng.choice(NOUN[cat])}"
        price = round(rng.uniform(6, 320), 2)
        weight = str(round(rng.uniform(0.1, 14), 1))
        if rng.random() < P_WEIGHT_JUNK:  # D5
            weight = rng.choice(WEIGHT_JUNK)
        rows.append([pid, name, cat, price, weight, 0.0])
        pid += 1
    return rows


def build_customers(rng):
    rows, seen, cid = [], set(), 20000
    while len(rows) < N_BASE_CUSTOMERS:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = name.lower().replace(" ", ".") + f"{rng.randint(1, 999)}@example.com"
        if email in seen:
            continue
        seen.add(email)
        signup = date(2019, 1, 1) + timedelta(days=rng.randint(0, 2100))
        rows.append([cid, name, email, signup, rng.choice(STATES), 0])
        cid += 1
    for src in rng.sample(rows[:], N_DUP_CUSTOMERS):  # D6: same person, new account
        rows.append([cid, src[1], src[2], date(2024, 1, 1) + timedelta(days=rng.randint(0, 300)),
                     src[4], 0])
        cid += 1
    return rows


def build_orders(rng, customers, products):
    orders, items, payments, shipments, returns, reviews = [], [], [], [], [], []
    oid, iid, pay_id, ship_id, ret_id, rev_id = 60000, 700000, 800000, 900000, 950000, 400000
    cust_ids = [c[0] for c in customers]
    for _ in range(N_ORDERS):
        placed = rand_dt(rng, datetime(2024, 11, 4), datetime(2025, 3, 8, 23, 59))
        web = rng.random() < P_WEB
        stored = placed + UTC_OFFSET if web else placed  # D7
        cust = rng.choice(cust_ids)
        if rng.random() < P_ORPHAN_ORDER:  # D8: deleted customer account
            cust = 29000 + rng.randint(0, 500)
        lines = []
        for p in rng.sample(products, rng.randint(1, 4)):
            lines.append((iid, oid, p[0], rng.choices([1, 2, 3, 4], [0.6, 0.25, 0.1, 0.05])[0], p[3]))
            iid += 1
        fee = rng.choice([0.0, 5.99, 9.99])
        total = round(sum(l[3] * l[4] for l in lines) + fee, 2)

        status, delivered_ok, ship = "PLACED", False, None
        if rng.random() < P_CANCELLED:
            status = "CANCELLED"
        else:
            shipped = placed + timedelta(hours=rng.randint(6, 72))
            if shipped <= WINDOW_END_DT:
                if rng.random() < P_LOST:
                    ship = (ship_id, oid, rng.choice(WAREHOUSES)[0], rng.choice(CARRIERS), shipped, None, "LOST")
                    status = "SHIPPED"
                else:
                    deliv = shipped + timedelta(hours=rng.randint(48, 168))
                    if deliv <= WINDOW_END_DT:
                        ship = (ship_id, oid, rng.choice(WAREHOUSES)[0], rng.choice(CARRIERS), shipped, deliv, "DELIVERED")
                        status, delivered_ok = "DELIVERED", True
                    else:
                        ship = (ship_id, oid, rng.choice(WAREHOUSES)[0], rng.choice(CARRIERS), shipped, None, "IN_TRANSIT")
                        status = "SHIPPED"
                ship_id += 1
                shipments.append(ship)

        any_return = False
        if delivered_ok:
            deliv_day = ship[5].date()
            for l in lines:
                if rng.random() < P_RETURN:
                    code = rng.randint(5, 9) if rng.random() < P_LEGACY_REASON else rng.randint(1, 4)  # D9
                    when = min(deliv_day + timedelta(days=rng.randint(2, 25)), WINDOW_END)
                    returns.append((ret_id, cust, l[0], when, code, round(l[3] * l[4], 2))); ret_id += 1
                    any_return = True
                if cust < 29000 and rng.random() < P_REVIEW:
                    raw = str(rng.choices([1, 2, 3, 4, 5], [0.06, 0.08, 0.16, 0.3, 0.4])[0])
                    if rng.random() < P_RATING_JUNK:  # D5
                        raw = rng.choice(["N/A", ""])
                    when = min(deliv_day + timedelta(days=rng.randint(1, 20)), WINDOW_END)
                    reviews.append((rev_id, cust, l[2], raw, when, rng.randint(0, 40))); rev_id += 1

        if status != "CANCELLED" or rng.random() < 0.6:
            if status == "CANCELLED":
                pstatus = "failed"
            else:
                pstatus = "refunded" if any_return else "captured"
            gateway = "LEGACY" if rng.random() < P_LEGACY_GATEWAY else "STRIPE"
            amount = round(total * 100, 2) if gateway == "LEGACY" else total  # D10
            payments.append((pay_id, oid, gateway, pstatus, amount)); pay_id += 1

        orders.append((oid, cust, stored, "WEB" if web else "CALL_CENTER", status, fee, total))
        items.extend(lines)
        oid += 1
    return orders, items, payments, shipments, returns, reviews


def finalize(rng, customers, products, orders, reviews):
    """D3: cached columns computed from truth, then corrupted for a share of rows."""
    count = {}
    for o in orders:
        count[o[1]] = count.get(o[1], 0) + 1
    cust_out = []
    for c in customers:
        n = count.get(c[0], 0)
        if rng.random() < P_STALE_CUSTOMER:
            n = max(0, n + rng.choice([-1, 1, 2]))
        cust_out.append((c[0], c[1], c[2], c[3], c[4], n))
    ratings = {}
    for r in reviews:
        if r[3] and r[3].isdigit():
            ratings.setdefault(r[2], []).append(int(r[3]))
    prod_out = []
    for p in products:
        vals = ratings.get(p[0], [])
        avg = round(sum(vals) / len(vals), 2) if vals else 0.0
        if rng.random() < P_STALE_PRODUCT:
            avg = round(rng.uniform(3.0, 5.0), 2)
        prod_out.append((p[0], p[1], p[2], p[3], p[4], avg))
    return cust_out, prod_out


# ---- assembly ----------------------------------------------------------------
def build_tables() -> dict:
    rng = random.Random(SEED)
    products = build_products(rng)
    customers = build_customers(rng)
    orders, items, payments, shipments, returns, reviews = build_orders(rng, customers, products)
    customers, products = finalize(rng, customers, products, orders, reviews)
    return {
        "categories": (["category_id", "name"], CATEGORIES),
        "warehouses": (["warehouse_id", "name", "state"], WAREHOUSES),
        "products": (["product_id", "name", "category_id", "price", "weight_kg", "avg_rating"], products),
        "customers": (["customer_id", "name", "email", "signup_date", "state", "order_count"], customers),
        "orders": (["order_id", "customer_id", "placed_at", "source", "status", "shipping_fee",
                    "total_amount"], orders),
        "order_items": (["item_id", "order_id", "product_id", "quantity", "unit_price"], items),
        "payments": (["payment_id", "order_id", "gateway", "status", "amount"], payments),
        "shipments": (["shipment_id", "order_ref", "warehouse_id", "carrier", "shipped_at",
                       "delivered_at", "status"], shipments),
        "returns": (["return_id", "buyer_id", "item_id", "returned_on", "reason_code",
                     "refund_amount"], returns),
        "reviews": (["review_id", "user_id", "product_id", "rating", "created_on",
                     "helpful_votes"], reviews),
    }


def generate(schema_path: Path) -> str:
    parts = [
        f"-- Synthetic e-commerce dataset v{DATASET_VERSION}, seed {SEED}.",
        "-- GENERATED by ecommerce/generate.py. Do not edit by hand.",
        schema_path.read_text().rstrip(),
        "SET autocommit = 0;",
        "START TRANSACTION;",
    ]
    for table, (cols, rows) in build_tables().items():
        parts.append(f"-- {table}: {len(rows)} rows")
        parts.extend(insert_stmts(table, cols, rows))
    parts += ["COMMIT;", "SET autocommit = 1;"]
    return "\n".join(parts) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="diff against committed seed.sql")
    args = ap.parse_args()
    here = Path(__file__).parent
    text = generate(here / "schema.sql")
    out = here / "seed.sql"
    if args.check:
        if not out.exists() or out.read_text() != text:
            print("seed.sql is out of date; rerun generate.py", file=sys.stderr)
            return 1
        print("seed.sql is up to date")
        return 0
    out.write_text(text)
    print(f"wrote {out} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
