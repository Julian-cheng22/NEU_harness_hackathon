"""Deterministic generator for the synthetic civil-litigation database.

    python legal/generate.py           -> writes legal/seed.sql
    python legal/generate.py --check   -> regenerates and diffs (exit 1 on drift)

All courts, judges, firms and parties are fictional. Bump DATASET_VERSION whenever
parameters or logic change: gold answers in questions.yaml must be re-verified.

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
N_BASE_PARTIES = 120
N_DUP_PARTIES = 8             # D6 (duplicated corporations)
N_JUDGES = 24
N_FIRMS = 12
N_ATTORNEYS = 60
N_CASES = 300
WINDOW_START = date(2024, 11, 4)
WINDOW_END = date(2025, 3, 8)
LAST_FILING_DAY = date(2025, 2, 10)
INVOICE_CUTOVER = date(2025, 1, 15)   # D10: invoices billed before this are in cents

P_RESOLVED = 0.55
P_APPEALED = 0.08             # among resolved
P_CONFIDENTIAL = 0.30         # among settled: closed_date NULL, award 'sealed' (D4/D5)
P_LEGACY_DISPOSITION = 0.10   # D9 (codes 5-9)
P_AWARD_JUNK = 0.15           # D5, among cases with an award
P_STALE_CASE = 0.18           # D3
P_ECF = 0.55                  # D7: ECF rows are stored in UTC
P_ORPHAN_ENTRY = 0.05         # D8
UTC_OFFSET = timedelta(hours=5)

COURTS = [(10, "Northern District Court", "District", "IL"),
          (11, "Southern District Court", "District", "NY"),
          (12, "Ninth Circuit Court of Appeals", "Circuit", "CA"),
          (13, "Harris County Superior Court", "State", "TX"),
          (14, "Suffolk County Superior Court", "State", "MA"),
          (15, "Eastern District Court", "District", "VA")]

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
         "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
         "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Nancy", "Matthew", "Lisa"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas",
        "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris"]
CORP_STEM = ["Apex", "Northwind", "Bluepeak", "Ironbridge", "Silverline", "Redwood",
             "Cobalt", "Harborview", "Summit", "Keystone", "Lakeshore", "Pinecrest"]
CORP_SUFFIX = ["Holdings LLC", "Industries Inc", "Partners LP", "Logistics Corp",
               "Systems Inc", "Group LLC", "Capital Corp", "Manufacturing Co"]
STATES = ["IL", "NY", "CA", "TX", "MA", "VA", "FL", "WA"]
FIRM_NAMES = ["Hale & Ross", "Mercer Lang", "Ostrander Pike", "Whitfield Cole", "Baxter Quinn",
              "Delacroix Moon", "Fenwick Shaw", "Garrett Voss", "Holloway Reed", "Ingram Stone",
              "Jasper Wolfe", "Kessler Dunn"]
CITIES = ["Chicago", "New York", "San Francisco", "Houston", "Boston", "Arlington"]
CASE_TYPES = ["civil", "contract", "employment", "ip", "personal_injury"]
ENTRY_TYPES = ["complaint", "answer", "motion", "order", "notice"]
HEARING_TYPES = ["status", "motion", "trial", "settlement"]
AWARD_JUNK = ["TBD", "N/A", "", "pending"]


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


def d2dt(d: date, hour: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour)


# ---- table builders --------------------------------------------------------
def build_people(rng):
    judges = [(100 + i, f"Hon. {rng.choice(FIRST)} {rng.choice(LAST)}",
               COURTS[i % len(COURTS)][0], date(2000, 1, 1) + timedelta(days=rng.randint(0, 8000)))
              for i in range(N_JUDGES)]
    firms = [(700 + i, FIRM_NAMES[i], rng.choice(CITIES)) for i in range(N_FIRMS)]
    attorneys = [(800 + i, f"{rng.choice(FIRST)} {rng.choice(LAST)}", rng.choice(firms)[0],
                  rng.randint(1985, 2022)) for i in range(N_ATTORNEYS)]
    return judges, firms, attorneys


def build_parties(rng):
    seen, rows, pid = set(), [], 30000
    while len(rows) < N_BASE_PARTIES:
        if rng.random() < 0.45:
            name, ptype = f"{rng.choice(CORP_STEM)} {rng.choice(CORP_SUFFIX)}", "corporation"
        else:
            name, ptype = f"{rng.choice(FIRST)} {rng.choice(LAST)}", "individual"
        state = rng.choice(STATES)
        if (name, ptype, state) in seen:
            continue
        seen.add((name, ptype, state))
        rows.append([pid, name, ptype, state])
        pid += 1
    corps = [r for r in rows if r[2] == "corporation"]
    for src in rng.sample(corps, N_DUP_PARTIES):  # D6: same entity, new surrogate key
        rows.append([pid, src[1], src[2], src[3]])
        pid += 1
    return [tuple(r) for r in rows]


def build_cases(rng, judges, attorneys):
    judges_by_court = {}
    for j in judges:
        judges_by_court.setdefault(j[2], []).append(j[0])
    rows = []
    span = (LAST_FILING_DAY - WINDOW_START).days
    for i in range(N_CASES):
        cid = 50000 + i
        court = rng.choice(COURTS)[0]
        judge = rng.choice(judges_by_court[court])
        atty = rng.choice(attorneys)[0]
        ctype = rng.choice(CASE_TYPES)
        filed = WINDOW_START + timedelta(days=rng.randint(0, span))
        closed, status, disp, award = None, "OPEN", None, None
        if rng.random() < P_RESOLVED:
            end = filed + timedelta(days=rng.randint(14, 90))
            if end <= WINDOW_END:
                closed = end
                status = "APPEALED" if rng.random() < P_APPEALED else "CLOSED"
                if status == "APPEALED":
                    disp = rng.choice([1, 2])
                elif rng.random() < P_LEGACY_DISPOSITION:
                    disp = rng.randint(5, 9)  # D9
                else:
                    disp = rng.choices([1, 2, 3, 4], [0.2, 0.15, 0.4, 0.25])[0]
                if disp in (1, 3):
                    if disp == 3 and rng.random() < P_CONFIDENTIAL:
                        closed, award = None, "sealed"  # D4/D5: confidential settlement
                    elif rng.random() < P_AWARD_JUNK:
                        award = rng.choice(AWARD_JUNK)  # D5
                    else:
                        award = str(rng.randint(10, 2000) * 1000)
        rows.append([cid, court, judge, atty, ctype, filed, closed, status, disp, award, 0])
    return rows


def build_case_parties(rng, cases, parties):
    rows, cp = [], 500000
    pids = [p[0] for p in parties]
    for c in cases:
        chosen = rng.sample(pids, 3 if rng.random() < 0.25 else 2)
        roles = ["plaintiff"] + ["defendant"] * (len(chosen) - 1)
        for pid, role in zip(chosen, roles):
            rows.append((cp, c[0], pid, role)); cp += 1
    return rows


def build_activity(rng, cases, attorneys, judges_by_court_lookup):
    docket, hearings, invoices = [], [], []
    e_id, h_id, inv_id = 200000, 300000, 400000
    firm_of = {a[0]: a[2] for a in attorneys}
    all_att = [a[0] for a in attorneys]
    for c in cases:
        cid, filed, closed = c[0], c[5], c[6]
        end_day = min(closed or WINDOW_END, WINDOW_END)
        lo, hi = d2dt(filed, 8), d2dt(end_day, 20)
        n = rng.randint(4, 16)
        times = sorted(rand_dt(rng, lo, hi) for _ in range(n))
        times[0] = d2dt(filed, rng.randint(8, 17))
        for k, t in enumerate(times):
            etype = "complaint" if k == 0 else rng.choice(ENTRY_TYPES[1:])
            ecf = rng.random() < P_ECF
            stored = t + UTC_OFFSET if ecf else t  # D7
            att = None if etype == "order" else rng.choice(all_att)
            case_no = cid
            if rng.random() < P_ORPHAN_ENTRY:  # D8
                case_no = 59000 + rng.randint(0, 500)
            docket.append((e_id, case_no, etype, stored, "ECF" if ecf else "CLERK", att))
            c[10] += 1 if case_no == cid else 0
            e_id += 1
        # hearings (scheduled_at is always court-local time)
        for _ in range(rng.randint(0, 3)):
            t = rand_dt(rng, d2dt(filed, 9), d2dt(min(closed or date(2025, 3, 31), date(2025, 3, 31)), 16))
            past = t < datetime(2025, 3, 9)
            status = rng.choices(["HELD", "CONTINUED", "CANCELLED"], [0.75, 0.15, 0.1])[0] if past else "SCHEDULED"
            hearings.append((h_id, cid, t, rng.choice(HEARING_TYPES), status, c[2])); h_id += 1
        # invoices
        if rng.random() < 0.85:
            for _ in range(rng.randint(1, 2)):
                billed = filed + timedelta(days=rng.randint(10, 60))
                if billed > WINDOW_END:
                    continue
                status = rng.choices(["paid", "unpaid", "void"], [0.55, 0.3, 0.15])[0]
                paid = billed + timedelta(days=rng.randint(10, 40)) if status == "paid" else None
                amount = round(rng.uniform(2500, 180000), 2)
                if billed < INVOICE_CUTOVER:  # D10: legacy billing stored cents
                    amount = round(amount * 100, 2)
                invoices.append((inv_id, cid, firm_of[c[3]], billed, paid, status, amount)); inv_id += 1
    return docket, hearings, invoices


def finalize_cases(rng, cases):
    """D3: cached docket count corrupted for ~18% of cases."""
    out = []
    for c in cases:
        n = c[10]
        if rng.random() < P_STALE_CASE:
            n = max(0, n + rng.choice([-2, -1, 1, 3]))
        out.append(tuple(c[:10]) + (n,))
    return out


# ---- assembly ----------------------------------------------------------------
def build_tables() -> dict:
    rng = random.Random(SEED)
    judges, firms, attorneys = build_people(rng)
    parties = build_parties(rng)
    cases = build_cases(rng, judges, attorneys)
    cparties = build_case_parties(rng, cases, parties)
    docket, hearings, invoices = build_activity(rng, cases, attorneys, None)
    cases = finalize_cases(rng, cases)
    return {
        "courts": (["court_id", "name", "level", "state"], COURTS),
        "judges": (["judge_id", "name", "court_id", "appointed_date"], judges),
        "law_firms": (["firm_id", "name", "city"], firms),
        "attorneys": (["attorney_id", "name", "firm_id", "bar_year"], attorneys),
        "parties": (["party_id", "name", "party_type", "state"], parties),
        "cases": (["case_id", "court_id", "judge_id", "lead_attorney_id", "case_type", "filed_date",
                   "closed_date", "status", "disposition_code", "award_amount", "num_filings"], cases),
        "case_parties": (["cp_id", "case_id", "party_id", "role"], cparties),
        "docket_entries": (["entry_id", "case_no", "entry_type", "filed_at", "source",
                            "attorney_id"], docket),
        "hearings": (["hearing_id", "matter_id", "scheduled_at", "hearing_type", "status",
                      "judge_id"], hearings),
        "fee_invoices": (["invoice_id", "case_id", "firm_id", "billed_at", "paid_at", "status",
                          "amount"], invoices),
    }


def generate(schema_path: Path) -> str:
    parts = [
        f"-- Synthetic civil-litigation dataset v{DATASET_VERSION}, seed {SEED}.",
        "-- GENERATED by legal/generate.py. Do not edit by hand.",
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
