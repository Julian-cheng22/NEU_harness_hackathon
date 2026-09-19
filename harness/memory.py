"""
The learned-schema artifact -- what the model worked out about THIS database,
proved against it, and kept.

WHY THIS EXISTS
---------------
Measured in round 1 of the review: roughly half the harness's lift came from
`glossary.py`, which is hand-written. That is the least portable rung of the
whole design -- it needs a human to have already written the convention down.
This module is the other path: the model proposes a claim, SQL proves or kills
it, and only survivors persist.

WHAT A CLAIM IS
---------------
Not a memory. A memory is something the model believes; a claim is something
the database confirmed. Every claim carries the query that proved it and the
number that query returned, so a reader (or a later run) can re-check it
instead of trusting it. That distinction is the whole safety story here:
round 1's worst regression (D8, 2/2 -> 0/2) came from a tool stating a wrong
number confidently. Writing that to a file would have made it permanent and
invisible.

STALENESS
---------
An artifact describes one version of one database. It is bound to
DATASET_VERSION and to a fingerprint of the live schema + row counts. If either
moves, the claims are suspect and `load()` says so rather than serving them
silently.

KNOWLEDGE MODES
---------------
HARNESS_KNOWLEDGE selects where the harness's business knowledge comes from:

  curated     the hand-written GLOSSARY, plus the hardcoded defect hints in
              execute.py and profile.py. This is the original harness, and the
              DEFAULT, so existing behaviour and existing tests are unchanged.
  discovered  only this artifact. Every curated hint is gated OFF -- see
              `curated_hints_enabled()`. Without that gating the comparison is
              worthless, because execute.py's _DIRTY_COLUMNS alone hands the
              model the entire defect list on every single query.
  both        the union. Useful in a demo, useless as evidence.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .db import DbConfig, connect

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ARTIFACT = ROOT / "data" / "learned_schema.yaml"

VALID_MODES = ("curated", "discovered", "both")
VALID_DECISIONS = ("pending", "approved", "rejected", "edited")


def artifact_path() -> Path:
    from . import dataset
    default = DEFAULT_ARTIFACT if dataset.name() == "saas" else dataset.data_dir() / "learned_schema.yaml"
    return Path(os.getenv("HARNESS_ARTIFACT", str(default)))


def knowledge_mode() -> str:
    """Which knowledge source the harness is running on.

    Defaults to `curated` on purpose: this module must not change the
    behaviour of the existing arm just by being importable.
    """
    mode = os.getenv("HARNESS_KNOWLEDGE", "curated").strip().lower()
    return mode if mode in VALID_MODES else "curated"


def curated_hints_enabled() -> bool:
    """Whether the hand-written defect hints scattered through the harness fire.

    Three places hold curated knowledge that has nothing to do with the
    glossary, and all three have to be gated together or the `discovered` arm
    is contaminated:

      * execute.py  _DIRTY_COLUMNS -- a per-column defect crib sheet attached
        to every query result.
      * profile.py  known_pairs    -- the hardcoded customers.plan_tier ->
        subscriptions.tier staleness check.
      * execute.py  the INNER JOIN / cust_id orphan warning.
    """
    return knowledge_mode() in ("curated", "both")


def learned_claims_enabled() -> bool:
    return knowledge_mode() in ("discovered", "both")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Review:
    """The human-adjudication slot.

    Deliberately a field on the claim rather than a separate store: a decision
    that can drift out of sync with the thing it decided about is worse than no
    decision. `pending` is the honest default -- nothing here has been looked
    at by a person until someone says so.
    """

    decision: str = "pending"
    reviewer: str | None = None
    note: str | None = None
    reviewed_at: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self.decision == "rejected"


@dataclass
class Claim:
    id: str
    name: str                       # the business term, e.g. "plan tier"
    finding: str                    # one sentence: what is wrong
    columns: list[str] = field(default_factory=list)
    guidance: str = ""              # how to write SQL correctly given the finding
    verification_sql: str = ""
    evidence: float | None = None   # what verification_sql returned
    status: str = "unverified"      # verified | rejected | unverified
    reason: str = ""                # why it was rejected, or a verification note
    flags: list[str] = field(default_factory=list)   # anti-gaming observations
    taxonomy: str = ""              # which defect class prompted it
    review: Review = field(default_factory=Review)

    @property
    def is_active(self) -> bool:
        """Verified by SQL and not vetoed by a human.

        Note the asymmetry: a human can VETO a machine-verified claim, and can
        APPROVE an unverified one (that is how a convention like a timezone
        rule, which no query can prove, gets in). Machine verification and
        human judgement are different axes, not a single score.
        """
        if self.review.decision == "rejected":
            return False
        if self.review.decision in ("approved", "edited"):
            return True
        return self.status == "verified"


@dataclass
class Artifact:
    dataset_version: str = ""
    db_fingerprint: str = ""
    model: str = ""
    discovered_at: str = ""
    taxonomy_version: str = ""
    claims: list[Claim] = field(default_factory=list)
    # Discovery-run bookkeeping: proposals, rejections, token cost.
    stats: dict[str, Any] = field(default_factory=dict)

    def active(self) -> list[Claim]:
        return [c for c in self.claims if c.is_active]

    def by_id(self, claim_id: str) -> Claim | None:
        want = claim_id.strip().upper()
        return next((c for c in self.claims if c.id.upper() == want), None)

    def next_id(self) -> str:
        return f"C{len(self.claims) + 1:02d}"


# ---------------------------------------------------------------------------
# Fingerprinting -- so a stale artifact announces itself
# ---------------------------------------------------------------------------
def db_fingerprint(cfg: DbConfig | None = None) -> str:
    """A stable hash of the schema shape plus per-table row counts.

    Row counts are included because a claim like "25 accounts disagree" is
    about the DATA, not just the columns. Re-seeding with a different generator
    version must invalidate the artifact even when the DDL is untouched.
    """
    cfg = cfg or DbConfig()
    h = hashlib.sha256()
    with connect(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name AS t, column_name AS c, column_type AS ty
                FROM information_schema.columns
                WHERE table_schema = %s
                ORDER BY table_name, ordinal_position
                """,
                (cfg.database,),
            )
            for r in cur.fetchall():
                h.update(f"{r['t']}.{r['c']}:{r['ty']}|".encode())

            cur.execute(
                "SELECT table_name AS t FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
                "ORDER BY table_name",
                (cfg.database,),
            )
            tables = [r["t"] for r in cur.fetchall()]

        for t in tables:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM `{t}`")
                row = cur.fetchone() or {}
            h.update(f"{t}={row.get('n')}|".encode())
    return h.hexdigest()[:16]


def dataset_version() -> str:
    """Read DATASET_VERSION out of the generator without importing it.

    data/generate.py pulls in the whole generation stack; this only needs one
    string, and an import cycle through the data layer is not worth it.
    """
    from . import dataset
    gen = dataset.data_dir() / "generate.py"
    try:
        for line in gen.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATASET_VERSION"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def load(path: Path | None = None) -> Artifact:
    """Load the artifact. A missing file is an empty artifact, not an error --
    the `curated` arm never writes one and must not trip over its absence."""
    p = path or artifact_path()
    if not p.exists():
        return Artifact()

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    claims: list[Claim] = []
    for c in raw.get("claims") or []:
        rv = c.pop("review", None) or {}
        cols = c.get("columns") or []
        if isinstance(cols, str):
            cols = [s.strip() for s in cols.split(",") if s.strip()]
        c["columns"] = cols
        known = {f for f in Claim.__dataclass_fields__ if f != "review"}
        claims.append(Claim(
            **{k: v for k, v in c.items() if k in known},
            review=Review(**{k: v for k, v in rv.items()
                             if k in Review.__dataclass_fields__}),
        ))

    return Artifact(
        dataset_version=raw.get("dataset_version", ""),
        db_fingerprint=raw.get("db_fingerprint", ""),
        model=raw.get("model", ""),
        discovered_at=raw.get("discovered_at", ""),
        taxonomy_version=raw.get("taxonomy_version", ""),
        claims=claims,
        stats=raw.get("stats") or {},
    )


def save(art: Artifact, path: Path | None = None) -> Path:
    p = path or artifact_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "dataset_version": art.dataset_version,
        "db_fingerprint": art.db_fingerprint,
        "model": art.model,
        "discovered_at": art.discovered_at or datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": art.taxonomy_version,
        "stats": art.stats,
        "claims": [
            {**{k: v for k, v in asdict(c).items() if k != "review"},
             "review": asdict(c.review)}
            for c in art.claims
        ],
    }
    p.write_text(
        "# Generated by `python -m harness.discover` -- question-blind.\n"
        "# Machine contract. Human-readable view: `python -m harness.review --render`.\n"
        + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return p


def staleness(art: Artifact, cfg: DbConfig | None = None) -> str | None:
    """None when the artifact matches the live database, else why it does not."""
    if not art.claims:
        return None
    want_v, want_f = dataset_version(), db_fingerprint(cfg)
    if art.dataset_version and art.dataset_version != want_v:
        return (f"artifact was built against dataset {art.dataset_version}, "
                f"live database is {want_v}")
    if art.db_fingerprint and art.db_fingerprint != want_f:
        return ("database fingerprint changed since discovery "
                f"({art.db_fingerprint} -> {want_f})")
    return None
