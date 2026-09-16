"""
Tests for question-blind discovery: the artifact, the verification contract,
and the knowledge-mode gating.

Two of these matter more than the rest.

`test_verification_rejects_*` are the anti-gaming suite. The discovery agent is
asked to produce both a claim and the proof of that claim, which is an obvious
incentive to write a query that cannot fail. If these stop failing, a model can
write anything into the artifact and the harness will believe it forever.

`test_discovered_mode_hides_curated_*` protect the experiment itself. Three
places in the harness hold hand-written defect knowledge, and if any one of
them leaks into the `discovered` arm, that arm is quietly reading the answers
and the A/B measures nothing. That failure is invisible in the score -- the
number just looks good -- which is exactly why it needs a test.

    .venv\\Scripts\\python -m pytest tests/test_discovery.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from harness import discover, execute, glossary, memory, profile, verify  # noqa: E402
from harness.db import DbConfig  # noqa: E402


@pytest.fixture
def cfg():
    return DbConfig()


@pytest.fixture
def mode(monkeypatch):
    """Set HARNESS_KNOWLEDGE and drop the learned-terms cache around it."""
    def _set(value: str):
        monkeypatch.setenv("HARNESS_KNOWLEDGE", value)
        glossary._LEARNED_CACHE.clear()
    yield _set
    glossary._LEARNED_CACHE.clear()


@pytest.fixture
def artifact_at(tmp_path, monkeypatch):
    def _set(art: memory.Artifact) -> Path:
        p = tmp_path / "learned.yaml"
        memory.save(art, p)
        monkeypatch.setenv("HARNESS_ARTIFACT", str(p))
        glossary._LEARNED_CACHE.clear()
        return p
    return _set


def _claim(**kw) -> memory.Claim:
    base = dict(id="C01", name="plan tier", finding="cached tier goes stale",
                columns=["customers.plan_tier"], guidance="join subscriptions",
                verification_sql="SELECT 1 AS evidence", evidence=25.0,
                status="verified", taxonomy="stale-copy")
    base.update(kw)
    return memory.Claim(**base)


# ---------------------------------------------------------------------------
# The verification contract -- anti-gaming
# ---------------------------------------------------------------------------
def test_verification_accepts_a_real_defect(cfg):
    """The D5 proof: junk values in a text column, isolated by a predicate."""
    v = verify.verify_claim(
        "SELECT COUNT(*) AS evidence FROM usage_events "
        "WHERE event_value IS NOT NULL AND event_value NOT REGEXP '^-?[0-9]+([.][0-9]+)?$'",
        ["usage_events.event_value"], cfg)
    assert v.ok, v.reason
    assert v.evidence and v.evidence > 0


def test_verification_accepts_a_real_join_defect(cfg):
    """The D3 proof: a cache disagreeing with its source across a join."""
    v = verify.verify_claim(
        "SELECT COUNT(*) AS evidence FROM customers a "
        "JOIN subscriptions b ON b.customer_id = a.customer_id "
        "WHERE b.ended_on IS NULL AND a.plan_tier <> b.tier",
        ["customers.plan_tier", "subscriptions.tier"], cfg)
    assert v.ok, v.reason
    assert v.evidence == 25


def test_verification_rejects_constant_proof(cfg):
    """`SELECT 1 AS evidence` references no table and proves nothing."""
    v = verify.verify_claim("SELECT 1 AS evidence", ["customers.plan_tier"], cfg)
    assert not v.ok
    assert "no table" in v.reason


def test_verification_rejects_tautological_proof(cfg):
    """A bare COUNT(*) is true of every non-empty table."""
    v = verify.verify_claim("SELECT COUNT(*) AS evidence FROM customers",
                            ["customers.plan_tier"], cfg)
    assert not v.ok
    assert "WHERE" in v.reason


def test_verification_rejects_off_topic_proof(cfg):
    """A real, discriminating query about columns the claim never mentions."""
    v = verify.verify_claim(
        "SELECT COUNT(*) AS evidence FROM invoices WHERE status = 'void'",
        ["usage_events.event_value"], cfg)
    assert not v.ok
    assert "claimed column" in v.reason


def test_verification_rejects_zero_evidence(cfg):
    """A proof that finds nothing disproves its own claim."""
    v = verify.verify_claim(
        "SELECT COUNT(*) AS evidence FROM invoices WHERE status = 'nope'",
        ["invoices.status"], cfg)
    assert not v.ok
    assert v.evidence == 0


def test_verification_rejects_wrong_shape(cfg):
    v = verify.verify_claim(
        "SELECT status AS evidence FROM invoices GROUP BY status",
        ["invoices.status"], cfg)
    assert not v.ok
    assert "1 row" in v.reason

    v = verify.verify_claim(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'void'",
        ["invoices.status"], cfg)
    assert not v.ok
    assert "evidence" in v.reason


def test_verification_rejects_writes(cfg):
    v = verify.verify_claim("DROP TABLE customers", ["customers.plan_tier"], cfg)
    assert not v.ok
    assert "refused" in v.reason


def test_verification_flags_whole_table_evidence(cfg):
    """Legitimate for a whole-column property, but also what a disguised
    tautology looks like -- so flag it for a human rather than reject it."""
    v = verify.verify_claim(
        "SELECT COUNT(*) AS evidence FROM customers WHERE customer_id > 0",
        ["customers.customer_id"], cfg)
    assert v.ok
    assert any("every row" in f for f in v.flag_list())


# ---------------------------------------------------------------------------
# Question-blindness
# ---------------------------------------------------------------------------
def test_discovery_never_reads_the_question_set():
    """Structural guarantee, not a promise in a docstring.

    If someone wires questions.yaml into the discovery prompt, the artifact is
    fitted to the test set and the headline number becomes meaningless while
    still looking fine. Catch it here.
    """
    import ast

    tree = ast.parse((ROOT / "harness" / "discover.py").read_text(encoding="utf-8"))
    # Strip docstrings; ast has already dropped comments. Otherwise this test
    # trips over the paragraph in discover.py that explains the rule.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body.pop(0)
    code = ast.unparse(tree)

    assert "questions.yaml" not in code
    assert "gold_sql" not in code
    assert "questions" not in code.lower()


def test_taxonomy_names_no_real_columns():
    """The taxonomy supplies defect CLASSES, not this database's answers."""
    text = " ".join(f"{d.title} {d.description}" for d in discover.TAXONOMY).lower()
    for leaked in ("plan_tier", "event_value", "acct_id", "cust_id", "paid_at",
                   "reason_code", "event_ts", "currency_minor", "is_current"):
        assert leaked not in text, f"taxonomy leaks the answer {leaked!r}"


# ---------------------------------------------------------------------------
# Artifact round-trip and review state
# ---------------------------------------------------------------------------
def test_artifact_round_trip(tmp_path):
    art = memory.Artifact(dataset_version="1.0.0", db_fingerprint="abc",
                          claims=[_claim(), _claim(id="C02", name="revenue")])
    p = memory.save(art, tmp_path / "a.yaml")
    back = memory.load(p)
    assert [c.id for c in back.claims] == ["C01", "C02"]
    assert back.claims[0].columns == ["customers.plan_tier"]
    assert back.claims[0].review.decision == "pending"


def test_human_veto_deactivates_a_verified_claim():
    c = _claim(status="verified")
    assert c.is_active
    c.review.decision = "rejected"
    assert not c.is_active


def test_human_approval_activates_an_unverified_claim():
    """The slot for conventions no query can prove -- a timezone rule, a tax
    treatment. The machine flags the anomaly; the person names it."""
    c = _claim(status="unverified", evidence=None)
    assert not c.is_active
    c.review.decision = "approved"
    assert c.is_active


def test_rejected_claims_are_kept_not_deleted(tmp_path):
    """A rejected proposal is the evidence of gaming behaviour. Deleting it
    would erase the only record that the model tried."""
    art = memory.Artifact(claims=[_claim(status="rejected", reason="tautology")])
    back = memory.load(memory.save(art, tmp_path / "a.yaml"))
    assert back.claims[0].status == "rejected"
    assert not back.claims[0].is_active


def test_staleness_detects_a_changed_fingerprint(cfg):
    art = memory.Artifact(dataset_version=memory.dataset_version(),
                          db_fingerprint="deadbeef", claims=[_claim()])
    assert "fingerprint" in (memory.staleness(art, cfg) or "")

    art.db_fingerprint = memory.db_fingerprint(cfg)
    assert memory.staleness(art, cfg) is None


# ---------------------------------------------------------------------------
# Knowledge-mode gating -- the experiment's integrity
# ---------------------------------------------------------------------------
def test_curated_is_the_default(monkeypatch):
    monkeypatch.delenv("HARNESS_KNOWLEDGE", raising=False)
    assert memory.knowledge_mode() == "curated"
    assert memory.curated_hints_enabled()
    assert not memory.learned_claims_enabled()


def test_curated_mode_is_unchanged_by_an_artifact(mode, artifact_at):
    """An artifact on disk must not alter the curated arm at all."""
    artifact_at(memory.Artifact(claims=[_claim(name="totally made up")]))
    mode("curated")
    assert glossary.active_glossary() is glossary.GLOSSARY
    assert glossary.learned_terms() == []


def test_discovered_mode_hides_curated_glossary(mode, artifact_at):
    artifact_at(memory.Artifact(claims=[_claim()]))
    mode("discovered")
    names = {t.name for t in glossary.active_glossary()}
    assert names == {"plan tier"}
    # The curated entry for the same term must not bleed through.
    assert not any("nightly job" in t.definition for t in glossary.active_glossary())


def test_discovered_mode_hides_curated_dirty_columns(mode):
    """execute.py's _DIRTY_COLUMNS is a bigger knowledge channel than the
    glossary: it names every defect, by column, on every query."""
    mode("curated")
    curated = execute._defect_notes("select max(event_value) from usage_events")
    assert any("event_value" in n for n in curated)

    mode("discovered")
    assert execute._defect_notes("select max(event_value) from usage_events") == []


def test_discovered_mode_hides_hardcoded_staleness(mode, cfg):
    """profile.py names the exact stale column in this dataset."""
    mode("curated")
    p = profile.profile_column("customers", "plan_tier", cfg)
    assert any("STALE CACHE" in w for w in p.warnings)

    mode("discovered")
    p = profile.profile_column("customers", "plan_tier", cfg)
    assert not any("STALE CACHE" in w for w in p.warnings)


def test_generic_probes_survive_gating(mode, cfg):
    """Type drift, duplicates and value domains are schema-independent
    instrumentation -- any engineer runs them on any warehouse. Only the
    dataset-specific lookups are gated, or discovery would have no instruments."""
    mode("discovered")
    p = profile.profile_column("usage_events", "event_value", cfg)
    assert any("TYPE DRIFT" in w for w in p.warnings)


def test_learned_claims_reach_the_model_through_the_same_channel(mode, artifact_at):
    """Same trigger, same placement as the curated hints -- only the content
    differs. Holding the channel fixed is what makes the A/B about knowledge."""
    artifact_at(memory.Artifact(claims=[
        _claim(name="usage volume", columns=["usage_events.event_value"],
               finding="stored as text with junk")]))
    mode("discovered")
    notes = execute._defect_notes("select max(event_value) from usage_events")
    assert any("event_value" in n and "junk" in n for n in notes)


def test_retrieval_finds_a_learned_claim(mode, artifact_at):
    artifact_at(memory.Artifact(claims=[
        _claim(name="usage volume", columns=["usage_events.event_value"])]))
    mode("discovered")
    hits = glossary.resolve_term("what is the highest usage volume recorded?")
    assert [t.name for t in hits] == ["usage volume"]


def test_retrieval_returns_the_no_guessing_message_when_empty(mode, artifact_at):
    """The empty result is load-bearing: it tells the model not to assume a
    convention, which is the correct behaviour for an unknown term."""
    artifact_at(memory.Artifact(claims=[]))
    mode("discovered")
    assert "Do NOT assume" in glossary.format_for_model(
        glossary.resolve_term("how is revenue calculated"))
