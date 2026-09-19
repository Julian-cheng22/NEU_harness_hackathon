"""Deterministic generator for the synthetic short-video feed database.

    python tiktok_feed/generate.py           -> writes tiktok_feed/seed.sql
    python tiktok_feed/generate.py --check   -> regenerates and diffs (exit 1 on drift)

All creators, users and videos are fictional; the schema is only TikTok-style.
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
DATASET_LABEL = "short-video feed"
DIRNAME = "tiktok_feed"

# ---- frozen parameters ---------------------------------------------------
N_CREATORS = 60
N_USERS = 400
N_DUP_USERS = 8               # D6 (second account on the same device)
N_SOUNDS = 40
N_VIDEOS = 500
N_IMPRESSIONS = 12000
N_LIKES = 5000
N_COMMENTS = 2500
N_FOLLOWS = 1500
N_REPORTS = 250
N_PROMOS = 120
WINDOW_START = datetime(2024, 11, 4)
WINDOW_END = datetime(2025, 3, 8, 23, 59)

P_STALE_CREATOR = 0.18        # D3
P_STALE_VIDEO = 0.25          # D3
P_REMOVED = 0.10
P_PRIVATE = 0.08
P_REMOVED_NO_TS = 0.40        # D4: removed but removed_at NULL (legacy takedowns)
P_DURATION_JUNK = 0.06        # D5
P_CLIENT = 0.55               # D7: CLIENT rows are stored in UTC
P_ORPHAN_LIKE = 0.05          # D8
P_LEGACY_REASON = 0.12        # D9 (codes 5-9)
P_LEGACY_BILLING = 0.30       # D10: LEGACY billing stores cents
UTC_OFFSET = timedelta(hours=5)

CATEGORIES = ["comedy", "dance", "food", "tech", "sports", "music", "education"]
COUNTRIES = ["US", "GB", "BR", "ID", "MX", "DE", "JP", "PH"]
ADJ = ["happy", "cosmic", "silent", "wild", "lucky", "neon", "tiny", "brave", "sunny", "cool"]
NOUN = ["panda", "fox", "chef", "coder", "dancer", "hiker", "owl", "surfer", "gamer", "baker"]
SOUND_WORDS = ["Sunrise", "Midnight", "Wave", "Groove", "Echo", "Pulse", "Drift", "Spark"]
DURATION_JUNK = ["N/A", "", "live"]


def handle(rng):
    return f"{rng.choice(ADJ)}_{rng.choice(NOUN)}{rng.randint(1, 999)}"


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
    sounds = [(800 + i, f"{rng.choice(SOUND_WORDS)} {rng.choice(SOUND_WORDS)} {i}", rng.randint(10, 60))
              for i in range(N_SOUNDS)]
    creators = [[3000 + i, "@" + handle(rng), rng.choice(COUNTRIES),
                 date(2020, 1, 1) + timedelta(days=rng.randint(0, 1600)), 0] for i in range(N_CREATORS)]
    users = []
    for i in range(N_USERS):
        users.append([100000 + i, handle(rng), rng.choice(COUNTRIES),
                      date(2019, 1, 1) + timedelta(days=rng.randint(0, 2100)), f"dev-{i:05d}"])
    uid = 100000 + N_USERS
    for src in rng.sample(users[:], N_DUP_USERS):  # D6: second account, same device
        users.append([uid, handle(rng), src[2], date(2024, 6, 1) + timedelta(days=rng.randint(0, 200)), src[4]])
        uid += 1
    return sounds, creators, users


def build_videos(rng, creators, sounds):
    rows, truth_dur = [], {}
    for i in range(N_VIDEOS):
        vid = 5000000 + i
        posted = rand_dt(rng, datetime(2024, 9, 1), datetime(2025, 3, 4))
        dur = rng.randint(5, 180)
        text = str(dur)
        if rng.random() < P_DURATION_JUNK:  # D5
            text = rng.choice(DURATION_JUNK)
        r = rng.random()
        status, removed = "PUBLISHED", None
        if r < P_REMOVED:
            status = "REMOVED"
            if rng.random() >= P_REMOVED_NO_TS:  # D4
                removed = min(posted + timedelta(days=rng.randint(1, 60)), WINDOW_END)
        elif r < P_REMOVED + P_PRIVATE:
            status = "PRIVATE"
        truth_dur[vid] = dur
        rows.append([vid, rng.choice(creators)[0], rng.choice(sounds)[0], posted, text,
                     rng.choice(CATEGORIES), status, removed, 0])
    return rows, truth_dur


def build_events(rng, users, videos, truth_dur, creators):
    uids = [u[0] for u in users]
    vinfo = {v[0]: v for v in videos}
    vids = list(vinfo)
    impressions, likes, comments, follows, reports, promos = [], [], [], [], [], []
    iid = 1000000
    while len(impressions) < N_IMPRESSIONS:
        v = vinfo[rng.choice(vids)]
        lo = max(v[3], WINDOW_START)
        hi = min(v[7] or WINDOW_END, WINDOW_END)
        if hi <= lo:
            continue
        t = rand_dt(rng, lo, hi)
        client = rng.random() < P_CLIENT
        stored = t + UTC_OFFSET if client else t  # D7
        dur_ms = truth_dur[v[0]] * 1000
        watch = min(dur_ms, int(rng.uniform(0.05, 1.3) * dur_ms))
        impressions.append((iid, rng.choice(uids), v[0], stored, "CLIENT" if client else "SERVER",
                            watch, 1 if watch >= 0.9 * dur_ms else 0)); iid += 1
    lid = 2000000
    for _ in range(N_LIKES):
        v = vinfo[rng.choice(vids)]
        t = rand_dt(rng, max(v[3], WINDOW_START), WINDOW_END)
        vid = v[0]
        if rng.random() < P_ORPHAN_LIKE:  # D8: liked video was hard-deleted
            vid = 5900000 + rng.randint(0, 500)
        likes.append((lid, rng.choice(uids), vid, t)); lid += 1
    cid = 3000000
    for _ in range(N_COMMENTS):
        v = vinfo[rng.choice(vids)]
        comments.append((cid, rng.choice(uids), v[0], rand_dt(rng, max(v[3], WINDOW_START), WINDOW_END),
                         rng.randint(1, 250))); cid += 1
    seen, fid = set(), 4000000
    while len(follows) < N_FOLLOWS:
        pair = (rng.choice(uids), rng.choice(creators)[0])
        if pair in seen:
            continue
        seen.add(pair)
        follows.append((fid, pair[0], pair[1], date(2024, 9, 1) + timedelta(days=rng.randint(0, 190)))); fid += 1
    rid = 6000000
    for _ in range(N_REPORTS):
        code = rng.randint(5, 9) if rng.random() < P_LEGACY_REASON else rng.randint(1, 4)  # D9
        reports.append((rid, rng.choice(vids), rng.choice(uids), code,
                        date(2024, 11, 4) + timedelta(days=rng.randint(0, 124)))); rid += 1
    pid = 600000
    for _ in range(N_PROMOS):
        legacy = rng.random() < P_LEGACY_BILLING
        budget = round(rng.uniform(20, 2500), 2)
        if legacy:
            budget = round(budget * 100, 2)  # D10
        promos.append((pid, rng.choice(vids), "LEGACY" if legacy else "ADS_V2",
                       rng.choices(["active", "paused", "ended"], [0.4, 0.2, 0.4])[0], budget,
                       date(2024, 11, 4) + timedelta(days=rng.randint(0, 120)))); pid += 1
    return impressions, likes, comments, follows, reports, promos


def finalize(rng, creators, videos, likes, follows):
    """D3: cached counts computed from truth, then corrupted for a share of rows."""
    real = {v[0] for v in videos}
    like_n, fol_n = {}, {}
    for l in likes:
        if l[2] in real:
            like_n[l[2]] = like_n.get(l[2], 0) + 1
    for f in follows:
        fol_n[f[2]] = fol_n.get(f[2], 0) + 1
    v_out = []
    for v in videos:
        n = like_n.get(v[0], 0)
        if rng.random() < P_STALE_VIDEO:
            n = max(0, n + rng.choice([-3, -1, 2, 5]))
        v_out.append(tuple(v[:8]) + (n,))
    c_out = []
    for c in creators:
        n = fol_n.get(c[0], 0)
        if rng.random() < P_STALE_CREATOR:
            n = max(0, n + rng.choice([-2, -1, 3, 6]))
        c_out.append((c[0], c[1], c[2], c[3], n))
    return c_out, v_out


# ---- assembly ----------------------------------------------------------------
def build_tables() -> dict:
    rng = random.Random(SEED)
    sounds, creators, users = build_base(rng)
    videos, truth_dur = build_videos(rng, creators, sounds)
    imp, likes, comments, follows, reports, promos = build_events(rng, users, videos, truth_dur, creators)
    creators, videos = finalize(rng, creators, videos, likes, follows)
    return {
        "sounds": (["sound_id", "title", "duration_sec"], sounds),
        "creators": (["creator_id", "handle", "country", "signup_date", "follower_count"], creators),
        "users": (["user_id", "handle", "country", "signup_date", "device_id"], [tuple(u) for u in users]),
        "videos": (["video_id", "creator_id", "sound_id", "posted_at", "duration_sec", "category",
                    "status", "removed_at", "like_count"], videos),
        "impressions": (["impression_id", "uid", "video_id", "shown_at", "source", "watch_ms",
                         "completed"], imp),
        "likes": (["like_id", "viewer_id", "video_id", "liked_at"], likes),
        "comments": (["comment_id", "commenter_id", "video_id", "created_at", "text_len"], comments),
        "follows": (["follow_id", "follower_id", "creator_id", "followed_at"], follows),
        "reports": (["report_id", "video_id", "reporter_id", "reason_code", "reported_on"], reports),
        "promotions": (["promo_id", "video_id", "billing_system", "status", "budget", "start_date"], promos),
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
