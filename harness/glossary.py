"""
Business-term resolution.

The profiler finds defects that are visible in the data: junk in a text column,
a cache that disagrees with its source, a NULL that spans two meanings. But
some defects are NOT statistically visible at all:

  D7  usage_events.event_ts is US/Eastern when source='batch' and UTC otherwise.
      Nothing about the values reveals that. Both look like plausible timestamps.
  D10 invoices.amount excludes tax; deals.amount includes it. Both are just
      positive decimals. No amount of profiling recovers the distinction.

That knowledge lives in people's heads, or in a wiki nobody reads. This module
is where it gets written down in a form the model can query -- the thing every
company should have and almost none do.

Honest framing for the demo: this is curated, not discovered. The harness does
not magically infer tax semantics. What it does is make institutional knowledge
retrievable at the moment the model needs it, instead of absent. That is a real
and defensible contribution -- do not oversell it as inference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Term:
    name: str
    definition: str
    columns: list[str] = field(default_factory=list)
    sql_hint: str = ""
    aliases: list[str] = field(default_factory=list)
    defect_ids: list[str] = field(default_factory=list)

    def format_for_model(self) -> str:
        lines = [f"Term: {self.name}", f"  meaning : {self.definition}"]
        if self.columns:
            lines.append(f"  columns : {', '.join(self.columns)}")
        if self.sql_hint:
            lines.append("  how to compute it correctly:")
            lines += [f"    {ln}" for ln in self.sql_hint.strip().splitlines()]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The glossary. Every entry should map to a real question someone would ask.
# ---------------------------------------------------------------------------
GLOSSARY: list[Term] = [
    Term(
        name="revenue",
        aliases=["invoiced revenue", "billings", "collected", "sales"],
        definition=(
            "Money actually invoiced, NET of tax. Lives in invoices.amount -- but "
            "that column mixes units: it is CENTS when currency_minor=1, DOLLARS "
            "when currency_minor=0, and UNKNOWN when currency_minor IS NULL. "
            "Invoices issued before 2025-07-01 came from the legacy biller and are "
            "in cents; use that to resolve the NULLs. Never SUM the raw column."
        ),
        columns=["invoices.amount", "invoices.currency_minor", "invoices.issued_at"],
        defect_ids=["D10"],
        sql_hint="""
SUM(
  CASE
    WHEN currency_minor = 1 THEN amount / 100
    WHEN currency_minor = 0 THEN amount
    -- unit flag lost: fall back to which biller issued it
    WHEN issued_at < '2025-07-01' THEN amount / 100
    ELSE amount
  END
) AS revenue_usd
-- and exclude voided invoices:  WHERE status <> 'void'
""",
    ),
    Term(
        name="deal value",
        aliases=["pipeline", "bookings", "contract value", "acv"],
        definition=(
            "deals.amount, always USD and always TAX-INCLUSIVE (grossed up 8%). "
            "This is NOT comparable to invoices.amount, which is tax-exclusive. "
            "Divide deal amounts by 1.08 before comparing the two, or you will "
            "report a ~8% gap that does not exist."
        ),
        columns=["deals.amount", "invoices.amount"],
        defect_ids=["D10"],
        sql_hint="deals.amount / 1.08  -- net of tax, comparable to invoices.amount",
    ),
    Term(
        name="event timestamp",
        aliases=["event_ts", "usage time", "when the event happened", "timezone"],
        definition=(
            "usage_events.event_ts is stored NAIVE with no timezone column. Rows "
            "with source='batch' were written in US/Eastern local time (UTC-4); "
            "every other source is UTC. Roughly 20% of events are batch, so any "
            "day-boundary or 'last 24 hours' filter is wrong by 4 hours for a "
            "fifth of the data unless you normalise first."
        ),
        columns=["usage_events.event_ts", "usage_events.source"],
        defect_ids=["D7"],
        sql_hint="""
CASE WHEN source = 'batch'
     THEN event_ts + INTERVAL 4 HOUR   -- US/Eastern -> UTC
     ELSE event_ts
END AS event_ts_utc
""",
    ),
    Term(
        name="usage volume",
        aliases=["event_value", "usage", "consumption"],
        definition=(
            "usage_events.event_value is the magnitude of an event, stored as "
            "VARCHAR. It contains 'N/A', empty strings and NULLs alongside real "
            "numbers, and MySQL coerces the junk to 0 without error. Which "
            "operations that breaks matters: MAX/MIN and ORDER BY compare "
            "LEXICALLY (so 'N/A' outranks '400'), AVG keeps the junk rows in the "
            "denominator and is biased downward, and COUNT(event_value) counts "
            "them. SUM is the exception -- it is accidentally correct, because "
            "zero adds nothing. Filter and CAST anyway, so the query is right "
            "for the reason you think it is."
        ),
        columns=["usage_events.event_value"],
        defect_ids=["D5"],
        sql_hint="""
-- correct for every aggregate, not just the forgiving ones:
MAX(CASE WHEN event_value REGEXP '^[0-9]+(\\\\.[0-9]+)?$'
         THEN CAST(event_value AS DECIMAL(18,4)) END) AS max_usage
-- Report how many rows you excluded; it is ~6% and reviewers will ask.
""",
    ),
    Term(
        name="plan tier",
        aliases=["tier", "plan", "subscription level", "package"],
        definition=(
            "subscriptions.tier is the SOURCE OF TRUTH. customers.plan_tier is a "
            "denormalized cache refreshed by a nightly job that has been silently "
            "failing; it disagrees with the live value for ~18% of accounts. "
            "Always join to subscriptions."
        ),
        columns=["subscriptions.tier", "customers.plan_tier"],
        defect_ids=["D3"],
        sql_hint="JOIN subscriptions s ON s.customer_id = c.customer_id AND s.ended_on IS NULL",
    ),
    Term(
        name="active customer",
        aliases=["active", "current customer", "live account"],
        definition=(
            "A customer with a subscription where ended_on IS NULL AND "
            "status = 'active'. customers.is_active is a separate, weaker flag "
            "that does not track cancellations reliably. Note that "
            "subscriptions.status uses active/paused/cancelled -- a DIFFERENT "
            "vocabulary from invoices.status (paid/unpaid/void) and deals.status "
            "(open/won/lost), even though all three columns are called 'status'."
        ),
        columns=["subscriptions.ended_on", "subscriptions.status", "customers.is_active"],
        defect_ids=["D2", "D3"],
        sql_hint="WHERE s.ended_on IS NULL AND s.status = 'active'",
    ),
    Term(
        name="paid invoice",
        aliases=["paid", "settled", "outstanding", "receivables", "unpaid"],
        definition=(
            "Use invoices.status = 'paid', NOT paid_at IS NOT NULL. About 9% of "
            "genuinely paid invoices have a NULL paid_at because the payment "
            "webhook never backfilled the timestamp. Treating paid_at IS NULL as "
            "'unpaid' overstates outstanding receivables."
        ),
        columns=["invoices.status", "invoices.paid_at"],
        defect_ids=["D4"],
        sql_hint="WHERE status = 'unpaid'   -- outstanding\nWHERE status = 'paid'     -- settled (paid_at may still be NULL)",
    ),
    Term(
        name="churn",
        aliases=["churned", "cancelled", "lost customer", "attrition"],
        definition=(
            "churn_log is an append-only HISTORICAL log, not current state. Rows "
            "with recovered_on IS NOT NULL are accounts that came back -- they are "
            "NOT currently churned. COUNT(*) over churn_log is not the churn count. "
            "reason_code runs 1-9, but the column comment only documents 1-4."
        ),
        columns=["churn_log.recovered_on", "churn_log.reason_code"],
        defect_ids=["D9"],
        sql_hint="WHERE recovered_on IS NULL   -- still churned",
    ),
    Term(
        name="churn reason",
        aliases=["reason_code", "why they left", "churn reason code"],
        definition=(
            "Documented in the column comment: 1=price, 2=missing_features, "
            "3=support, 4=competitor. UNDOCUMENTED but present in the data: "
            "5, 6, 7, 8, 9. There is no lookup table and no authoritative source "
            "for 5-9. Report them as 'unknown code N' -- do NOT guess a label."
        ),
        columns=["churn_log.reason_code"],
        defect_ids=["D9"],
    ),
    Term(
        name="customer key",
        aliases=["customer_id", "cust_id", "acct_id", "account", "how to join"],
        definition=(
            "The same customer key is spelled THREE ways: customers.customer_id "
            "and subscriptions.customer_id; invoices.cust_id and "
            "support_tickets.cust_id; deals.acct_id. All three refer to "
            "customers.customer_id. Additionally, ~5% of support_tickets.cust_id "
            "point at customers that were hard-deleted -- an INNER JOIN silently "
            "drops those tickets."
        ),
        columns=["customers.customer_id", "invoices.cust_id",
                 "support_tickets.cust_id", "deals.acct_id"],
        defect_ids=["D1", "D8"],
        sql_hint="LEFT JOIN customers c ON c.customer_id = t.cust_id  -- LEFT, to keep orphans visible",
    ),
    Term(
        name="list price",
        aliases=["price", "catalog price", "current price"],
        definition=(
            "product_catalog is versioned: each sku has several rows over time. "
            "The live row is the one with effective_to IS NULL. The is_current "
            "flag is unreliable -- a botched backfill left it disagreeing with "
            "effective_to for some SKUs. Joining on sku alone fans out rows and "
            "mixes in superseded prices."
        ),
        columns=["product_catalog.effective_to", "product_catalog.is_current"],
        defect_ids=["D3"],
        sql_hint="WHERE effective_to IS NULL   -- NOT: WHERE is_current = 1",
    ),
    Term(
        name="company",
        aliases=["company name", "account name", "organisation", "duplicate"],
        definition=(
            "customers contains duplicate organisations under different "
            "customer_ids, with name variants like 'Acme' vs 'Acme, Inc.'. "
            "Grouping by customer_id splits a company's totals across its "
            "duplicate rows. Normalise the name before grouping."
        ),
        columns=["customers.company_name", "customers.customer_id"],
        defect_ids=["D6"],
        sql_hint="GROUP BY LOWER(REGEXP_REPLACE(company_name, '[^a-zA-Z0-9]', ''))",
    ),
]

_BY_NAME = {t.name.lower(): t for t in GLOSSARY}


# ---------------------------------------------------------------------------
# Learned knowledge -- the same slot, filled by discovery instead of by hand
# ---------------------------------------------------------------------------
# A discovered claim and a curated Term carry the same payload (what the thing
# means, which columns, how to compute it), so the answering agent does not
# need to know which one it got. That is the point: `discovered` is a drop-in
# replacement for this file, which is what makes the A/B meaningful.
_LEARNED_CACHE: dict[str, object] = {}


def _claim_to_term(c) -> Term:
    definition = c.finding
    if c.evidence is not None:
        # Carry the proof through to the model. "25 accounts disagree" is
        # actionable in a way that "this column is unreliable" is not, and it
        # is the number a reader can re-check.
        definition += f" (verified against this database: {c.evidence:,.0f} affected)"
    aliases = sorted({col.split(".")[-1].lower() for col in c.columns})
    return Term(name=c.name, definition=definition, columns=c.columns,
                sql_hint=c.guidance, aliases=aliases, defect_ids=[c.taxonomy])


def learned_terms() -> list[Term]:
    """Active claims from the artifact, as Terms. Empty unless the mode asks."""
    from . import memory
    if not memory.learned_claims_enabled():
        return []
    p = memory.artifact_path()
    key = f"{p}:{p.stat().st_mtime if p.exists() else 0}"
    if _LEARNED_CACHE.get("key") != key:
        _LEARNED_CACHE["key"] = key
        _LEARNED_CACHE["terms"] = [_claim_to_term(c) for c in memory.load(p).active()]
    return list(_LEARNED_CACHE["terms"])  # type: ignore[arg-type]


def active_glossary() -> list[Term]:
    """The terms this run is allowed to see, per HARNESS_KNOWLEDGE."""
    from . import memory
    mode = memory.knowledge_mode()
    if mode == "curated":
        return GLOSSARY
    if mode == "discovered":
        return learned_terms()
    return [*GLOSSARY, *learned_terms()]


# Question-shaped filler. Without this, a single shared word like "the" scores
# a match and the model gets handed confidently irrelevant advice -- which is
# strictly worse than returning nothing, because the no-match path explicitly
# tells it not to assume a convention.
_STOPWORDS = {
    "the", "and", "for", "what", "how", "was", "were", "are", "does", "did",
    "with", "from", "this", "that", "these", "those", "you", "your", "its",
    "has", "have", "had", "all", "any", "get", "when", "where", "which",
    "who", "why", "can", "out", "per", "than", "then", "there", "their",
    "them", "they", "into", "over", "under", "about", "much", "many", "some",
    "show", "give", "list", "find", "tell", "need", "want", "should", "would",
}

# One shared CONTENT word is a real signal ("events in the last 24 hours" ->
# event timestamp). One shared stopword is not, which is why _STOPWORDS above
# has to do its job before this threshold can be this permissive.
_MIN_SCORE = 1.5


def _stem(w: str) -> str:
    """Crudest useful stemmer: fold plurals and past tense.

    Without this, 'customers' does not match 'customer' and 'churned' does not
    match 'churn' -- which is most of the vocabulary a user actually types.
    A real stemmer is not worth the dependency for a 12-entry glossary.
    """
    for suffix, keep in (("ies", 3), ("ing", 3), ("ed", 2), ("s", 1)):
        if w.endswith(suffix) and len(w) - keep >= 3:
            if suffix == "s" and w.endswith("ss"):
                continue          # 'address' is not a plural
            base = w[: len(w) - keep]
            return base + "y" if suffix == "ies" else base
    return w


def _tokens(s: str) -> set[str]:
    return {_stem(w) for w in re.split(r"[^a-z0-9]+", s.lower())
            if len(w) > 2 and w not in _STOPWORDS}


def resolve_term(query: str, limit: int = 3) -> list[Term]:
    """Look up business terms relevant to `query`.

    Scored rather than exact-matched: the model asks things like "how do I
    compute revenue per account", not "revenue".

    Returns [] when nothing scores above _MIN_SCORE. That empty result is
    load-bearing -- format_for_model() turns it into an explicit instruction
    not to guess, which is the correct behaviour for an unknown term.
    """
    q = query.lower().strip()
    terms = active_glossary()
    by_name = {t.name.lower(): t for t in terms}
    if q in by_name:
        return [by_name[q]]

    qt = _tokens(q)
    scored: list[tuple[float, Term]] = []
    for term in terms:
        score = 0.0
        if term.name.lower() in q:
            score += 10.0
        for alias in term.aliases:
            if alias.lower() in q:
                score += 6.0
        for col in term.columns:
            if col.split(".")[-1].lower() in q:
                score += 4.0
        overlap = qt & (_tokens(term.name) | _tokens(" ".join(term.aliases)))
        score += 1.5 * len(overlap)
        if score >= _MIN_SCORE:
            scored.append((score, term))

    scored.sort(key=lambda p: (-p[0], p[1].name))
    return [t for _, t in scored[:limit]]


def format_for_model(terms: list[Term]) -> str:
    if not terms:
        return ("No glossary entry matched. Do NOT assume a convention -- profile "
                "the columns involved and say explicitly what you assumed.")
    return "\n\n".join(t.format_for_model() for t in terms)


def terms_for_defect(defect_id: str) -> list[Term]:
    """Used by the eval report to show which glossary entry covers which defect."""
    return [t for t in GLOSSARY if defect_id in t.defect_ids]
