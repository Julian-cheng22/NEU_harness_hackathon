"""Deterministic generator for the synthetic hospital database.

    python medical/generate.py           -> writes medical/seed.sql
    python medical/generate.py --check   -> regenerates and diffs (exit 1 on drift)

Bump DATASET_VERSION whenever any parameter or generation logic changes:
gold answers in questions.yaml must be re-verified.

Time model. Data window is 2023-11-06 .. 2024-03-09, which sits entirely in
US Eastern *standard* time (UTC-5), so no DST logic is needed.
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
N_BASE_PATIENTS = 120
N_DUP_PATIENTS = 8            # D6
N_PROVIDERS = 40
N_ADMISSIONS = 420
N_INPATIENT = 14              # still in hospital at end of window
WINDOW_START = datetime(2023, 11, 6)
WINDOW_END = datetime(2024, 3, 9, 23, 59)
CLAIM_CUTOVER = date(2024, 1, 15)   # D10: claims billed before this are in cents

P_STALE_PATIENT = 0.18        # D3
P_DIED = 0.04
P_DIED_NULL_DISCH = 0.35      # D4 (dischtime NULL although patient died)
P_LEGACY_DISPOSITION = 0.10   # D9 (codes 5-9)
P_LAB_JUNK = 0.055            # D5
P_GLUCOSE_MMOL = 0.15         # D10
P_LAB_BATCH = 0.35            # D7 (LIS_BATCH rows are stored in UTC)
P_ORPHAN_RX = 0.05            # D8

UTC_OFFSET = timedelta(hours=5)  # UTC = local + 5h in the data window

DEPARTMENTS = [(10, "Emergency"), (11, "Cardiology"), (12, "Internal Medicine"),
               (13, "Surgery"), (14, "Oncology"), (15, "Neurology"),
               (16, "Pediatrics"), (17, "Intensive Care")]

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
         "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
         "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Nancy", "Matthew", "Lisa",
         "Anthony", "Betty", "Mark", "Helen", "Steven", "Sandra"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas",
        "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White",
        "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker"]

# (itemid, label, fluid, lo, hi, uom, decimals)
LAB_ITEMS = [
    (50001, "Glucose", "Blood", 70, 260, "mg/dL", 0),
    (50002, "Creatinine", "Blood", 0.5, 3.2, "mg/dL", 2),
    (50003, "Hemoglobin", "Blood", 8, 16, "g/dL", 1),
    (50004, "Sodium", "Blood", 130, 148, "mEq/L", 0),
    (50005, "Potassium", "Blood", 3.2, 5.6, "mEq/L", 1),
    (50006, "WBC", "Blood", 3, 18, "K/uL", 1),
    (50007, "Lactate", "Blood", 0.5, 5.0, "mmol/L", 1),
    (50008, "Troponin", "Blood", 0.0, 2.0, "ng/mL", 2),
]
GLUCOSE_MMOL_FACTOR = 18.016
LAB_JUNK = ["NEG", "pending", ">100", "", "N/A"]

ICD = ["I10", "E11.9", "J18.9", "N17.9", "I50.9", "K35.80", "J44.1", "A41.9",
       "I21.4", "G45.9", "C34.90", "N39.0", "R07.9", "E87.1"]
DRUGS = ["Metformin", "Lisinopril", "Atorvastatin", "Heparin", "Ceftriaxone",
         "Furosemide", "Insulin glargine", "Aspirin", "Vancomycin", "Morphine"]
PROC_CODES = ["0DJ08ZZ", "02HV33Z", "5A1935Z", "0W9G3ZZ", "4A023N6", "B211YZZ"]
INSURANCE = ["Medicare", "Medicaid", "Private", "Self-pay"]


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
def build_providers(rng):
    rows = []
    for i in range(N_PROVIDERS):
        role = rng.choices(["physician", "nurse", "technician"], [0.4, 0.45, 0.15])[0]
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        hire = date(2005, 1, 1) + timedelta(days=rng.randint(0, 6800))
        rows.append((200 + i, name, rng.choice(DEPARTMENTS)[0], role, hire))
    return rows


def build_patients_base(rng):
    seen, rows = set(), []
    pid = 10000
    while len(rows) < N_BASE_PATIENTS:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        dob = date(1935, 1, 1) + timedelta(days=rng.randint(0, 33000))
        if (name, dob) in seen:
            continue
        seen.add((name, dob))
        sex = "F" if rng.random() < 0.5 else "M"
        rows.append([pid, name, dob, sex, rng.choice(INSURANCE)])
        pid += 1
    # D6: same person registered again under a fresh surrogate key
    for src in rng.sample(rows[:], N_DUP_PATIENTS):
        rows.append([pid, src[1], src[2], src[3], rng.choice(INSURANCE)])
        pid += 1
    return rows


def build_admissions(rng, patients, physicians):
    rows = []
    last_admit_start = WINDOW_END - timedelta(days=14)
    for i in range(N_ADMISSIONS):
        hadm = 20000 + i
        subj = rng.choice(patients)[0]
        dept = rng.choice(DEPARTMENTS)[0]
        atype = rng.choices(["EMERGENCY", "ELECTIVE", "URGENT"], [0.55, 0.3, 0.15])[0]
        attending = rng.choice(physicians)
        if i < N_INPATIENT:
            admit = rand_dt(rng, WINDOW_END - timedelta(days=6), WINDOW_END - timedelta(days=1))
            rows.append([hadm, subj, admit, None, atype, "ADMITTED", "IN_HOSPITAL", None, dept, attending])
            continue
        admit = rand_dt(rng, WINDOW_START, last_admit_start)
        disch = admit + timedelta(hours=rng.randint(24, 14 * 24))
        if rng.random() < P_DIED:
            if rng.random() < P_DIED_NULL_DISCH:
                disch = None  # D4
            rows.append([hadm, subj, admit, disch, atype, "EXPIRED", "DIED", 4, dept, attending])
        else:
            if rng.random() < P_LEGACY_DISPOSITION:
                disp, loc = rng.randint(5, 9), "OTHER"  # D9
            else:
                disp = rng.choices([1, 2, 3], [0.65, 0.2, 0.15])[0]
                loc = {1: "HOME", 2: "SNF", 3: "REHAB"}[disp]
            rows.append([hadm, subj, admit, disch, atype, "DISCHARGED", loc, disp, dept, attending])
    rng.shuffle(rows)  # inpatients should not all sit at the lowest hadm_ids
    ids = sorted(r[0] for r in rows)
    for r, hid in zip(rows, ids):
        r[0] = hid
    rows.sort(key=lambda r: r[0])
    return rows


def finalize_patients(rng, patients, admissions):
    """D3: cached columns computed from truth, then corrupted for ~18%."""
    count = {}
    last = {}
    for a in sorted(admissions, key=lambda r: r[2]):
        count[a[1]] = count.get(a[1], 0) + 1
        last[a[1]] = a[8]
    out = []
    for pid, name, dob, sex, ins in patients:
        total = count.get(pid, 0)
        dept = last.get(pid)
        if rng.random() < P_STALE_PATIENT:
            total = max(0, total + rng.choice([-1, 1, 2]))
            dept = rng.choice(DEPARTMENTS)[0]
        out.append((pid, name, dob, sex, ins, total, dept))
    return out


def build_clinical(rng, admissions, providers):
    physicians = [p[0] for p in providers if p[3] == "physician"]
    all_prov = [p[0] for p in providers]
    dx, labs, rx, procs, claims = [], [], [], [], []
    dx_id, lab_id, rx_id, proc_id, claim_id = 300000, 100000, 400000, 500000, 600000
    for a in admissions:
        hadm, subj, admit, disch = a[0], a[1], a[2], a[3]
        end = disch or WINDOW_END
        if end <= admit:
            end = admit + timedelta(hours=24)
        # diagnoses
        for seq, code in enumerate(rng.sample(ICD, rng.randint(1, 4)), start=1):
            dx.append((dx_id, hadm, subj, code, seq)); dx_id += 1
        # lab draws
        for _ in range(rng.randint(2, 6)):
            t = rand_dt(rng, admit, end)
            batch = rng.random() < P_LAB_BATCH
            stored_t = t + UTC_OFFSET if batch else t  # D7
            for item in rng.sample(LAB_ITEMS, 4):
                itemid, label, _f, lo, hi, uom, dec = item
                val_num = round(rng.uniform(lo, hi), dec)
                text = None
                if rng.random() < P_LAB_JUNK:  # D5
                    text, val_num = rng.choice(LAB_JUNK), None
                elif label == "Glucose" and rng.random() < P_GLUCOSE_MMOL:  # D10
                    val_num = round(val_num / GLUCOSE_MMOL_FACTOR, 1)
                    uom = "mmol/L"
                if text is None:
                    text = str(int(val_num)) if dec == 0 and uom != "mmol/L" else str(val_num)
                labs.append((lab_id, subj, hadm, itemid, stored_t, text,
                             float(val_num) if val_num is not None else None, uom,
                             "LIS_BATCH" if batch else "EHR_ENTRY"))
                lab_id += 1
        # prescriptions (pat_id, not subject_id: D1)
        for _ in range(rng.randint(1, 3)):
            h = hadm
            if rng.random() < P_ORPHAN_RX:  # D8
                h = 29000 + rng.randint(0, 500)
            rx.append((rx_id, subj, h, rng.choice(DRUGS), rand_dt(rng, admit, end),
                       rng.choice(physicians))); rx_id += 1
        # procedures
        if rng.random() < 0.7:
            procs.append((proc_id, hadm, subj, rng.choice(PROC_CODES),
                          rand_dt(rng, admit, end), rng.choice(all_prov))); proc_id += 1
        # claims: ~90% of admissions billed
        if rng.random() < 0.9:
            billed = (disch or admit).date() + timedelta(days=rng.randint(3, 20))
            status = rng.choices(["paid", "submitted", "denied", "void"], [0.6, 0.2, 0.15, 0.05])[0]
            paid = billed + timedelta(days=rng.randint(10, 40)) if status == "paid" else None
            amount = round(rng.uniform(800, 25000), 2)
            if billed < CLAIM_CUTOVER:  # D10: legacy billing system stored cents
                amount = round(amount * 100, 2)
            claims.append((claim_id, subj, hadm, billed, paid, status, amount)); claim_id += 1
    return dx, labs, rx, procs, claims


# ---- assembly ----------------------------------------------------------------
def build_tables() -> dict:
    """Return {table: (cols, rows)} in load order."""
    rng = random.Random(SEED)
    providers = build_providers(rng)
    physicians = [p[0] for p in providers if p[3] == "physician"]
    patients_base = build_patients_base(rng)
    admissions = build_admissions(rng, patients_base, physicians)
    patients = finalize_patients(rng, patients_base, admissions)
    dx, labs, rx, procs, claims = build_clinical(rng, admissions, providers)
    lab_items = [(i[0], i[1], i[2]) for i in LAB_ITEMS]
    return {
        "departments": (["dept_id", "name"], DEPARTMENTS),
        "providers": (["provider_id", "name", "dept_id", "role", "hire_date"], providers),
        "patients": (["subject_id", "name", "dob", "sex", "insurance",
                      "total_admissions", "last_dept_id"], patients),
        "admissions": (["hadm_id", "subject_id", "admittime", "dischtime", "admission_type",
                        "status", "discharge_location", "discharge_disposition",
                        "dept_id", "attending_id"], [tuple(a) for a in admissions]),
        "diagnoses": (["dx_id", "hadm_id", "subject_id", "icd_code", "seq_num"], dx),
        "lab_items": (["itemid", "label", "fluid"], lab_items),
        "lab_events": (["labevent_id", "subject_id", "hadm_id", "itemid", "charttime",
                        "value", "valuenum", "uom", "source"], labs),
        "prescriptions": (["rx_id", "pat_id", "hadm_id", "drug", "start_time",
                           "provider_id"], rx),
        "procedures": (["proc_id", "hadm_id", "subject_id", "proc_code", "performed_at",
                        "provider_id"], procs),
        "claims": (["claim_id", "patient_id", "hadm_id", "billed_at", "paid_at",
                    "status", "amount"], claims),
    }


def generate(schema_path: Path) -> str:
    parts = [
        f"-- Synthetic hospital dataset v{DATASET_VERSION}, seed {SEED}.",
        "-- GENERATED by medical/generate.py. Do not edit by hand.",
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
