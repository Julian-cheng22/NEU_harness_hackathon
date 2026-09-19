"""
The human-adjudication interface for discovered claims.

    python -m harness.review --list
    python -m harness.review --show C03
    python -m harness.review --approve C03 --reviewer jz --note "confirmed with finance"
    python -m harness.review --reject  C07 --reviewer jz --note "coincidence, not a rule"
    python -m harness.review --render            # -> data/learned_schema.md

SCOPE -- READ THIS BEFORE BUILDING A UI ON IT
----------------------------------------------
This is an INTERFACE, not a review product. The database here is synthetic and
its ten defects are known in advance, so there is nothing for a human to
genuinely adjudicate -- a reviewer would just be confirming what the generator
already wrote down. Building a real review UI against synthetic data would be
building it against a problem that does not exist.

What matters is that the seam is in the right place, because on real company
data this is where the system stops being automatic. So: the decision field is
first-class, it lives on the claim, `is_active` already honours it, and the
functions below are shaped to be called by an HTTP handler as easily as by this
CLI. Swapping argparse for a route is the whole remaining job.

WHY HUMANS ADJUDICATE CLAIMS AND NOT TRAJECTORIES
--------------------------------------------------
Grading a 12-step agent trace is slow and low-signal. Approving a one-line
claim that arrives with the query that proved it and the number it returned is
a few seconds of work, and it is the unit that actually gets reused.

THE TWO AXES DO NOT COLLAPSE
-----------------------------
Machine verification and human judgement are independent, and the data model
keeps them that way:

  * A human can VETO a machine-verified claim -- the query was right and the
    conclusion was still wrong.
  * A human can APPROVE a claim the machine could not verify. This is the slot
    for conventions no query can prove: a timezone rule, a tax treatment. The
    machine detects the anomaly; the person names it.

Collapsing these into one score would throw away exactly the cases that make a
human worth asking.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import memory  # noqa: E402


# ---------------------------------------------------------------------------
# API -- these are what an HTTP layer would call
# ---------------------------------------------------------------------------
def list_claims(status: str | None = None,
                decision: str | None = None,
                path: Path | None = None) -> list[dict[str, Any]]:
    """Claims as plain dicts, newest schema first. JSON-ready."""
    art = memory.load(path)
    out = []
    for c in art.claims:
        if status and c.status != status:
            continue
        if decision and c.review.decision != decision:
            continue
        out.append({
            "id": c.id,
            "name": c.name,
            "finding": c.finding,
            "columns": c.columns,
            "guidance": c.guidance,
            "verification_sql": c.verification_sql,
            "evidence": c.evidence,
            "status": c.status,
            "reason": c.reason,
            "flags": c.flags,
            "taxonomy": c.taxonomy,
            "decision": c.review.decision,
            "reviewer": c.review.reviewer,
            "note": c.review.note,
            "reviewed_at": c.review.reviewed_at,
            "active": c.is_active,
        })
    return out


def get_claim(claim_id: str, path: Path | None = None) -> dict[str, Any] | None:
    return next((c for c in list_claims(path=path)
                 if c["id"].upper() == claim_id.strip().upper()), None)


def set_review(claim_id: str, decision: str, reviewer: str | None = None,
               note: str | None = None, path: Path | None = None) -> dict[str, Any]:
    """Record a human decision. Raises ValueError on a bad id or decision."""
    if decision not in memory.VALID_DECISIONS:
        raise ValueError(f"decision must be one of {memory.VALID_DECISIONS}, "
                         f"got {decision!r}")
    art = memory.load(path)
    claim = art.by_id(claim_id)
    if claim is None:
        raise ValueError(f"no claim {claim_id!r} in {memory.artifact_path()}")

    claim.review.decision = decision
    claim.review.reviewer = reviewer
    claim.review.note = note
    claim.review.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    memory.save(art, path)
    return get_claim(claim_id, path) or {}


def queue(path: Path | None = None) -> list[dict[str, Any]]:
    """What a reviewer should look at, worst first.

    Ordering is the only opinion in this module: a FLAGGED claim (verified but
    suspicious, e.g. its proof covers the whole table) is the most valuable
    thing a person can look at, because it is exactly where mechanical
    verification is weakest. Rejected proposals come next -- they are where you
    see the model trying to game the contract. Clean verified claims last.
    """
    def rank(c: dict[str, Any]) -> tuple[int, str]:
        if c["decision"] != "pending":
            return (3, c["id"])
        if c["status"] == "verified" and c["flags"]:
            return (0, c["id"])
        if c["status"] == "rejected":
            return (1, c["id"])
        return (2, c["id"])

    return sorted(list_claims(path=path), key=rank)


def render_markdown(path: Path | None = None, out: Path | None = None) -> Path:
    """Human-readable view of the artifact.

    YAML is the machine contract; this is what a person reads. Same split the
    project already uses for data/defects.yaml and the README.
    """
    art = memory.load(path)
    from . import dataset
    dest = out or (dataset.data_dir() / "learned_schema.md")

    lines = [
        "# Learned schema knowledge",
        "",
        f"Discovered question-blind by `{art.model or '?'}` on "
        f"{art.discovered_at or '?'}.",
        f"Dataset `{art.dataset_version}`, fingerprint `{art.db_fingerprint}`, "
        f"taxonomy `{art.taxonomy_version}`.",
        "",
        "Every claim below carries the query that proved it and the number that "
        "query returned. Re-check rather than trust.",
        "",
    ]

    s = art.stats or {}
    if s:
        lines += [
            f"Proposed {s.get('proposed', 0)} | verified {s.get('verified', 0)} | "
            f"rejected {s.get('rejected', 0)} | duplicate {s.get('duplicate', 0)}",
            "",
        ]

    active = [c for c in art.claims if c.is_active]
    lines += ["| id | claim | evidence | class | decision |",
              "|---|---|---:|---|---|"]
    for c in active:
        ev = f"{c.evidence:,.0f}" if c.evidence is not None else "-"
        lines.append(f"| {c.id} | {c.name} | {ev} | {c.taxonomy} | "
                     f"{c.review.decision} |")
    if not active:
        lines.append("| - | *(no active claims)* | | | |")
    lines.append("")

    for c in art.claims:
        mark = {"verified": "OK", "rejected": "REJECTED",
                "unverified": "UNVERIFIED"}.get(c.status, c.status)
        lines += [f"## {c.id} — {c.name}  `{mark}`", ""]
        if c.finding:
            lines += [c.finding, ""]
        if c.columns:
            lines += [f"**Columns:** `{'`, `'.join(c.columns)}`", ""]
        if c.evidence is not None:
            lines += [f"**Evidence:** {c.evidence:,.0f}", ""]
        if c.verification_sql:
            lines += ["**Proof:**", "", "```sql", c.verification_sql, "```", ""]
        if c.guidance:
            lines += ["**Guidance given to the answering model:**", "",
                      "```", c.guidance, "```", ""]
        if c.status == "rejected":
            lines += [f"**Rejected because:** {c.reason}", ""]
        for f in c.flags:
            lines += [f"> FLAGGED: {f}", ""]
        lines += [f"**Human decision:** {c.review.decision}"
                  + (f" by {c.review.reviewer}" if c.review.reviewer else "")
                  + (f" — {c.review.note}" if c.review.note else ""), ""]

    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_row(c: dict[str, Any]) -> None:
    ev = f"{c['evidence']:,.0f}" if c["evidence"] is not None else "-"
    mark = {"verified": "OK  ", "rejected": "REJ ", "unverified": "??  "}.get(
        c["status"], "    ")
    flag = " [FLAGGED]" if c["flags"] else ""
    act = "active" if c["active"] else "inactive"
    print(f"  {mark}{c['id']:<5}{c['name'][:30]:<32}{ev:>10}  "
          f"{c['taxonomy'][:22]:<24}{c['decision']:<9}{act}{flag}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Human review of discovered claims.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--queue", action="store_true",
                    help="Same as --list, ordered by what most needs a human.")
    ap.add_argument("--show", metavar="ID")
    ap.add_argument("--approve", metavar="ID")
    ap.add_argument("--reject", metavar="ID")
    ap.add_argument("--reviewer", default=None)
    ap.add_argument("--note", default=None)
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args()

    if args.approve or args.reject:
        cid = args.approve or args.reject
        decision = "approved" if args.approve else "rejected"
        try:
            c = set_review(cid, decision, args.reviewer, args.note)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"{c['id']} -> {c['decision']} (active={c['active']})")
        return 0

    if args.show:
        c = get_claim(args.show)
        if not c:
            print(f"error: no claim {args.show!r}", file=sys.stderr)
            return 1
        for k, v in c.items():
            print(f"  {k:<18}{v}")
        return 0

    if args.render:
        print(f"-> {render_markdown().relative_to(ROOT)}")
        return 0

    rows = queue() if args.queue else list_claims()
    if not rows:
        print("No claims. Run:  python -m harness.discover")
        return 0
    print(f"  {'':<4}{'id':<5}{'claim':<32}{'evidence':>10}  "
          f"{'class':<24}{'decision':<9}state")
    for c in rows:
        _print_row(c)
    n_active = sum(1 for c in rows if c["active"])
    print(f"\n  {len(rows)} claim(s), {n_active} active.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
