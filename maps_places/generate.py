"""Deterministic generator for the synthetic map-listings database.

    python maps_places/generate.py           -> writes maps_places/seed.sql
    python maps_places/generate.py --check   -> regenerates and diffs (exit 1 on drift)

All places, owners and users are fictional; the schema is only Google-Maps-style.
Bump DATASET_VERSION whenever parameters or logic change: gold answers in
questions.yaml must be re-verified.

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
DATASET_LABEL = "map-listings"
DIRNAME = "maps_places"

# ---- frozen parameters ---------------------------------------------------
N_BASE_PLACES = 300
N_DUP_PLACES = 8              # D6 (same business listed twice)
N_OWNERS = 80
N_USERS = 500
N_REVIEWS = 6000
N_PHOTOS = 1500
N_FLAGS = 200
N_ADS = 150
WINDOW_START = datetime(2024, 11, 4)
WINDOW_END = datetime(2025, 3, 8, 23, 59)

P_UNCLAIMED = 0.35            # owner_id NULL (single meaning, clean)
P_TEMP_CLOSED = 0.05
P_PERM_CLOSED = 0.10
P_CLOSED_NO_DATE = 0.35       # D4: permanently closed but closed_date NULL
P_STALE_PLACE = 0.20          # D3
P_RATING_JUNK = 0.04          # D5
P_SEATING_JUNK = 0.10         # D5
P_MOBILE = 0.6                # D7: MOBILE rows are stored in UTC
P_ORPHAN_REVIEW = 0.05        # D8
P_LEGACY_REASON = 0.12        # D9 (codes 5-9)
P_LEGACY_BILLING = 0.30       # D10: LEGACY billing stores cents
UTC_OFFSET = timedelta(hours=5)

CATEGORIES = [(10, "Restaurant"), (11, "Cafe"), (12, "Bar"), (13, "Gym"), (14, "Hotel"),
              (15, "Pharmacy"), (16, "Bookstore"), (17, "Park"), (18, "Museum"),
              (19, "Grocery"), (20, "Salon"), (21, "Gas Station")]
CITIES = ["Boston", "Chicago", "Seattle"]
HOODS = ["Downtown", "Riverside", "Old Town", "Harbor", "Uptown"]
FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
         "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas", "Taylor"]
STEM = ["Golden", "Blue", "Maple", "Harbor", "Sunset", "Corner", "Little", "Grand", "Green", "Copper"]
KIND = {10: ["Kitchen", "Bistro", "Grill"], 11: ["Coffee", "Roasters", "Cafe"], 12: ["Tap House", "Pub", "Lounge"],
        13: ["Fitness", "Gym", "Athletic Club"], 14: ["Inn", "Hotel", "Suites"], 15: ["Pharmacy", "Drugstore"],
        16: ["Books", "Bookshop"], 17: ["Park", "Gardens"], 18: ["Museum", "Gallery"],
        19: ["Market", "Grocery"], 20: ["Salon", "Barbers"], 21: ["Fuel", "Gas & Go"]}
STREETS = ["Main", "Oak", "Pine", "Cedar", "Elm", "Lake", "Hill", "Park", "River", "Union"]
SEATING_JUNK = ["N/A", "", "unknown"]


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
        return ("%.6f" % v).rstrip("0").rstrip(".")
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
def build_base(rng):
    hoods = []
    for i, city in enumerate(CITIES):
        for j, h in enumerate(HOODS):
            hoods.append((40 + i * 10 + j, city, h))
    owners = [(900 + i, f"{rng.choice(FIRST)} {rng.choice(LAST)}",
               f"owner{i}@example.com") for i in range(N_OWNERS)]
    users = [(20000 + i, f"{rng.choice(FIRST)} {rng.choice(LAST)}", rng.choice(CITIES),
              date(2016, 1, 1) + timedelta(days=rng.randint(0, 3200))) for i in range(N_USERS)]
    return hoods, owners, users


def build_places(rng, hoods, owners):
    rows, seen, pid = [], set(), 7000
    while len(rows) < N_BASE_PLACES:
        cat = rng.choice(CATEGORIES)[0]
        name = f"{rng.choice(STEM)} {rng.choice(KIND[cat])}"
        addr = f"{rng.randint(1, 999)} {rng.choice(STREETS)} St"
        if (name, addr) in seen:
            continue
        seen.add((name, addr))
        owner = None if rng.random() < P_UNCLAIMED else rng.choice(owners)[0]
        seats = str(rng.randint(8, 220))
        if rng.random() < P_SEATING_JUNK:  # D5
            seats = rng.choice(SEATING_JUNK)
        opened = date(2005, 1, 1) + timedelta(days=rng.randint(0, 7000))
        r, status, closed = rng.random(), "OPEN", None
        if r < P_PERM_CLOSED:
            status = "CLOSED_PERMANENTLY"
            if rng.random() >= P_CLOSED_NO_DATE:  # D4
                closed = date(2023, 1, 1) + timedelta(days=rng.randint(0, 800))
        elif r < P_PERM_CLOSED + P_TEMP_CLOSED:
            status = "TEMP_CLOSED"
        rows.append([pid, name, addr, cat, rng.choice(hoods)[0], owner, seats, status, opened, closed, 0.0, 0])
        pid += 1
    for src in rng.sample(rows[:], N_DUP_PLACES):  # D6: same business, second listing
        dup = list(src)
        dup[0] = pid
        dup[5] = None
        rows.append(dup)
        pid += 1
    return rows


def build_activity(rng, places, users):
    uids = [u[0] for u in users]
    pids = [p[0] for p in places]
    reviews, photos, flags, ads, hours = [], [], [], [], []
    rid = 400000
    for _ in range(N_REVIEWS):
        t = rand_dt(rng, WINDOW_START, WINDOW_END)
        mobile = rng.random() < P_MOBILE
        stored = t + UTC_OFFSET if mobile else t  # D7
        ref = rng.choice(pids)
        if rng.random() < P_ORPHAN_REVIEW:  # D8: listing was deleted
            ref = 8000 + rng.randint(0, 500)
        rating = str(rng.choices([1, 2, 3, 4, 5], [0.07, 0.08, 0.15, 0.3, 0.4])[0])
        if rng.random() < P_RATING_JUNK:  # D5
            rating = rng.choice(["N/A", ""])
        reviews.append((rid, ref, rng.choice(uids), rating, rng.randint(0, 900), stored,
                        "MOBILE" if mobile else "WEB")); rid += 1
    for i in range(N_PHOTOS):
        photos.append((500000 + i, rng.choice(pids), rng.choice(uids),
                       date(2024, 11, 4) + timedelta(days=rng.randint(0, 124)), rng.randint(80, 4500)))
    for i in range(N_FLAGS):
        code = rng.randint(5, 9) if rng.random() < P_LEGACY_REASON else rng.randint(1, 4)  # D9
        flags.append((600000 + i, rng.choice(pids), rng.choice(uids), code,
                      date(2024, 11, 4) + timedelta(days=rng.randint(0, 124))))
    for i in range(N_ADS):
        legacy = rng.random() < P_LEGACY_BILLING
        spend = round(rng.uniform(15, 1800), 2)
        if legacy:
            spend = round(spend * 100, 2)  # D10
        ads.append((700000 + i, rng.choice(pids), "LEGACY" if legacy else "ADS_V2",
                    rng.choices(["active", "paused", "ended"], [0.4, 0.2, 0.4])[0], spend,
                    date(2024, 11, 4) + timedelta(days=rng.randint(0, 120))))
    hid = 800000
    for p in places:
        if p[7] == "CLOSED_PERMANENTLY":
            continue
        for wd in range(7):
            if wd == 0 and rng.random() < 0.4:
                continue
            o = rng.choice([6, 7, 8, 9, 10])
            hours.append((hid, p[0], wd, f"{o:02d}:00:00", f"{rng.choice([17, 18, 20, 22, 23]):02d}:00:00")); hid += 1
    return reviews, photos, flags, ads, hours


def finalize(rng, places, reviews):
    """D3: cached rating and review count computed from truth, then corrupted for ~20%."""
    real = {p[0] for p in places}
    cnt, vals = {}, {}
    for r in reviews:
        if r[1] in real:
            cnt[r[1]] = cnt.get(r[1], 0) + 1
            if r[3].isdigit():
                vals.setdefault(r[1], []).append(int(r[3]))
    out = []
    for p in places:
        n = cnt.get(p[0], 0)
        v = vals.get(p[0], [])
        avg = round(sum(v) / len(v), 2) if v else 0.0
        if rng.random() < P_STALE_PLACE:
            n = max(0, n + rng.choice([-3, -1, 2, 5]))
            avg = round(rng.uniform(2.8, 5.0), 2)
        out.append(tuple(p[:10]) + (avg, n))
    return out


# ---- assembly ----------------------------------------------------------------
def build_tables() -> dict:
    rng = random.Random(SEED)
    hoods, owners, users = build_base(rng)
    places = build_places(rng, hoods, owners)
    reviews, photos, flags, ads, hours = build_activity(rng, places, users)
    places = finalize(rng, places, reviews)
    return {
        "categories": (["category_id", "name"], CATEGORIES),
        "neighborhoods": (["neighborhood_id", "city", "name"], hoods),
        "owners": (["owner_id", "name", "email"], owners),
        "places": (["place_id", "name", "address", "category_id", "neighborhood_id", "owner_id",
                    "seating_capacity", "status", "opened_date", "closed_date", "avg_rating",
                    "review_count"], places),
        "users": (["user_id", "name", "home_city", "joined_date"], users),
        "reviews": (["review_id", "place_ref", "reviewer_id", "rating", "text_len", "created_at",
                     "source"], reviews),
        "photos": (["photo_id", "poi_id", "uploader_id", "uploaded_on", "size_kb"], photos),
        "flags": (["flag_id", "listing_id", "reporter_id", "reason_code", "flagged_on"], flags),
        "ads": (["ad_id", "place_id", "billing_system", "status", "spend", "start_date"], ads),
        "opening_hours": (["hours_id", "place_id", "weekday", "open_time", "close_time"], hours),
    }


def generate(schema_path: Path) -> str:
    parts = [
        f"-- Synthetic {DATASET_LABEL} dataset v{DATASET_VERSION}, seed {SEED}.",
        f"-- GENERATED by {DIRNAME}/generate.py. Do not edit by hand.",
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
