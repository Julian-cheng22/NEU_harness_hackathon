"""
Question-blind schema discovery.

    python -m harness.discover            # discover, verify, write the artifact
    python -m harness.discover --classes stale-copy,type-drift
    python -m harness.discover --dry-run  # print, do not write

WHAT THIS IS
------------
The model is pointed at the database with no questions in sight and asked, one
defect class at a time, "is this kind of problem present here, and can you
prove it?" Survivors of `verify.verify_claim` are written to
`data/learned_schema.yaml` and become retrievable business knowledge for the
answering agent -- the same slot `glossary.py` fills by hand today.

QUESTION-BLINDNESS IS STRUCTURAL, NOT A PROMISE
-----------------------------------------------
This module never reads `data/questions.yaml`. It cannot: there is no import,
no path, no parameter through which an eval question can reach the discovery
prompt. That is deliberate and it is the single guardrail that keeps this from
being test-set fitting. Overfitting to the SCHEMA is the goal here -- it is
what a new analyst does in week one. Overfitting to the QUESTION SET would be
fraud, and it would be invisible in the final number.

If you ever add a parameter to this module that carries question text, the
experiment is void. Do not.

WHERE THE PRIOR KNOWLEDGE SITS -- BE HONEST ABOUT THIS
-------------------------------------------------------
The taxonomy below is prior knowledge. We are telling the model which KINDS of
defect to look for. What we are NOT telling it is which tables or columns carry
them, what the conventions are, or what the right SQL is -- all of which is
what `glossary.py` hardcodes today.

So the claim this supports is narrow and should be stated narrowly: a generic,
schema-independent defect taxonomy plus mechanical verification can recover
knowledge that is currently hand-written per database. The taxonomy ports to a
new company; the glossary does not.

WHY ONE PASS PER CLASS INSTEAD OF ONE FREE-ROAMING AGENT
---------------------------------------------------------
Tried the open-ended version first. A 9B given "find problems in this database"
wanders, re-profiles the same column, and spends its budget narrating. Ten
focused passes with a small step budget each are more reproducible, cost a
bounded amount, and make it obvious which class produced which claim -- which
is what the per-defect breakdown needs anyway.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import joins, memory, profile, schema_card, verify  # noqa: E402
from harness.db import DbConfig, run_query  # noqa: E402
from harness.llm import LLM  # noqa: E402

TAXONOMY_VERSION = "1.0.0"


@dataclass(frozen=True)
class DefectClass:
    id: str
    title: str
    description: str


# Schema-independent. No table names, no column names, no conventions. Each
# entry is the kind of thing a data engineer checks on any warehouse they are
# handed for the first time.
TAXONOMY: list[DefectClass] = [
    DefectClass(
        "naming-drift", "The same concept under different column names",
        "One real-world identifier spelled differently on different tables, so "
        "a name-based join misses some of them entirely. Look for columns that "
        "hold the same VALUES as a primary key elsewhere while being named "
        "nothing like it.",
    ),
    DefectClass(
        "vocabulary-collision", "The same column name meaning different things",
        "A column name reused across tables with a DIFFERENT set of allowed "
        "values on each. Filtering with a value from the wrong table's "
        "vocabulary returns zero rows, which reads as 'no data' rather than as "
        "an error. Compare the value domains of same-named columns.",
    ),
    DefectClass(
        "stale-copy", "A denormalised value that disagrees with its source",
        "A column that caches a value owned by another table. Caches go stale. "
        "Find pairs of columns -- one on a child table, one on the table that "
        "owns the concept -- that should agree for the same entity and do not. "
        "Count the disagreeing entities.",
    ),
    DefectClass(
        "overloaded-null", "A NULL that means more than one thing",
        "A nullable column where NULL covers two distinct situations ('did not "
        "happen' and 'happened but was not recorded'). Symptom: the NULL rows "
        "span more than one value of a status-like column on the same table.",
    ),
    DefectClass(
        "type-drift", "Numbers or dates stored as text",
        "A text column holding mostly numeric values plus some junk. MySQL "
        "coerces junk to 0 without raising, and comparisons run LEXICALLY, so "
        "aggregates and ORDER BY are silently wrong. Count the non-numeric "
        "values in text columns that look numeric.",
    ),
    DefectClass(
        "duplicate-entity", "One real thing under several keys",
        "The same organisation or person present under more than one surrogate "
        "key, usually with a punctuation or suffix variant of the name. Any "
        "GROUP BY on the key splits that entity's totals. Normalise case and "
        "punctuation and count the collisions.",
    ),
    DefectClass(
        "unit-inconsistency", "Mixed scales or tax treatment in numeric columns",
        "A money or measure column whose rows are not all in the same unit "
        "(minor vs major currency units), or two columns that look comparable "
        "but are not (one gross, one net). Symptoms: a bimodal magnitude "
        "distribution within one column, a flag column that selects the scale, "
        "or a constant ratio between two columns that should match.",
    ),
    DefectClass(
        "hidden-temporal-convention", "Timestamps with an undeclared timezone",
        "A naive timestamp column with no timezone column beside it, where "
        "some subgroup of rows was written in a different zone. Symptom: group "
        "the rows by a source-like column and compare the HOUR-OF-DAY "
        "distributions -- a shifted subgroup is the tell. You can detect the "
        "shift; you cannot read the intended zone off the data, so say so.",
    ),
    DefectClass(
        "referential-orphan", "Child rows pointing at parents that do not exist",
        "With no foreign keys declared, a child column can reference ids that "
        "were deleted. An INNER JOIN drops those rows silently and the counts "
        "disagree with a direct COUNT(*). Report the number of ROWS affected, "
        "not the number of distinct values -- they are different numbers.",
    ),
    DefectClass(
        "documentation-drift", "Comments that do not match the data",
        "A column COMMENT that documents a value set smaller than what the "
        "column actually contains. The documentation is confidently incomplete, "
        "which is worse than absent. Compare each commented column's stated "
        "domain against its real one.",
    ),
]

_BY_ID = {d.id: d for d in TAXONOMY}


_SYSTEM = """You are a data engineer auditing an unfamiliar company database. \
You are NOT answering anyone's question. Your only job is to find out whether \
one specific KIND of data defect is present here, and to PROVE it with SQL.

DEFECT CLASS UNDER INVESTIGATION: {title}
{description}

How to work:
  1. Use profile_column and run_probe to investigate. Be quick -- you have a
     small number of steps.
  2. If you find a real instance, call record_claim ONCE per distinct finding.
     Every claim MUST include `guidance`: the concrete SQL pattern that gets
     this right. A finding with no guidance is useless to the analyst who
     reads it later -- they will know something is wrong and not what to do.
  3. If this database does not have this defect, say so and stop. Reporting
     nothing is a correct and useful outcome. Do NOT invent a finding.

record_claim requires a proof, and the proof is checked mechanically:

  verification_sql MUST be a single SELECT returning EXACTLY ONE ROW with
  EXACTLY ONE COLUMN named `evidence`, holding the COUNT of rows or entities
  that exhibit the problem.

  It must reference at least one real table, must mention at least one of the
  columns your claim is about, and must contain a WHERE, HAVING, CASE, JOIN or
  GROUP BY that isolates the bad rows. A proof that counts a whole table proves
  nothing and will be rejected. A proof returning 0 disproves your own claim
  and will be rejected.

  Good:  SELECT COUNT(*) AS evidence FROM t WHERE col NOT REGEXP '^[0-9]+$'
  Bad:   SELECT 1 AS evidence
  Bad:   SELECT COUNT(*) AS evidence FROM t

{schema}
"""

_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "profile_column",
        "description": ("What is really in a column: null rate, value domain, "
                        "type drift, sample values."),
        "parameters": {
            "type": "object",
            "properties": {"table": {"type": "string"}, "column": {"type": "string"}},
            "required": ["table", "column"],
        },
    }},
    {"type": "function", "function": {
        "name": "run_probe",
        "description": "Run a read-only SELECT to investigate the data.",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    }},
    {"type": "function", "function": {
        "name": "record_claim",
        "description": ("Record one proven finding about this database. Only call "
                        "this when you have a verification_sql that isolates the "
                        "bad rows."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Short business term, e.g. 'plan tier'."},
                "finding": {"type": "string",
                            "description": "One sentence: what is wrong."},
                "columns": {"type": "string",
                            "description": "Comma-separated table.column list."},
                "guidance": {"type": "string",
                             "description": ("REQUIRED. The SQL pattern that gets "
                                             "this right, e.g. a CASE expression, a "
                                             "WHERE clause, or the join to use. This "
                                             "is what a later query-writing model "
                                             "will actually follow.")},
                "verification_sql": {"type": "string",
                                     "description": "SELECT ... AS evidence. One row."},
            },
            # `guidance` is required. Left optional, the model omitted it on
            # every single claim, and the discovered arm then carried findings
            # with no way to act on them -- while the curated glossary it is
            # being compared against ships a worked sql_hint for every term.
            # That is not a knowledge difference, it is a missing field.
            "required": ["name", "finding", "columns", "guidance",
                         "verification_sql"],
        },
    }},
]


def _fmt_rows(result: dict[str, Any], max_rows: int = 15) -> str:
    """Compact result rendering.

    Note this does NOT go through harness.execute.execute_sql, and that is on
    purpose: that path attaches the hand-written _DIRTY_COLUMNS crib sheet to
    every result, which would hand the discovery agent exactly the answers it
    is supposed to be finding for itself.
    """
    if not result.get("ok"):
        return f"QUERY FAILED\n{result.get('error')}\n\nFix it and try again."
    rows = result.get("rows") or []
    lines = [f"OK -- {result['row_count']} row(s)"]
    if rows:
        cols = list(rows[0].keys())
        lines.append(" | ".join(cols))
        for r in rows[:max_rows]:
            lines.append(" | ".join("NULL" if r[c] is None else str(r[c]) for c in cols))
        if len(rows) > max_rows:
            lines.append(f"... {len(rows) - max_rows} more")
    return "\n".join(lines)


def _split_columns(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    return [s.strip() for s in str(raw or "").split(",") if s.strip()]


@dataclass
class Proposal:
    """One record_claim call, before verification decides its fate."""
    taxonomy: str
    name: str
    finding: str
    columns: list[str]
    guidance: str
    verification_sql: str


def _run_class(llm: LLM, dc: DefectClass, card: str, cfg: DbConfig,
               max_steps: int, verbose: bool) -> tuple[list[Proposal], dict[str, int]]:
    """One focused pass. Returns its proposals and its token usage."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM.format(
            title=dc.title, description=dc.description, schema=card)},
        {"role": "user", "content":
            f"Audit this database for: {dc.title}. Investigate, then either "
            f"record_claim with a proof, or state that this database does not "
            f"have this defect."},
    ]

    proposals: list[Proposal] = []
    usage: dict[str, int] = {}

    for step in range(max_steps):
        try:
            resp = llm.chat(messages, tools=_TOOLS, temperature=0.0)
        except Exception as e:
            print(f"    ! LLM failed at step {step + 1}: {e}")
            break

        for k, v in (resp.usage or {}).items():
            if isinstance(v, int):
                usage[k] = usage.get(k, 0) + v

        if not resp.wants_tool:
            if verbose and resp.text:
                print(f"    . {resp.text.strip()[:160]}")
            break

        messages.append({
            "role": "assistant",
            "content": resp.text or None,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in resp.tool_calls
            ],
        })

        for tc in resp.tool_calls:
            args = tc.arguments or {}
            if tc.name == "record_claim":
                p = Proposal(
                    taxonomy=dc.id,
                    name=str(args.get("name") or "").strip() or dc.title,
                    finding=str(args.get("finding") or "").strip(),
                    columns=_split_columns(args.get("columns")),
                    guidance=str(args.get("guidance") or "").strip(),
                    verification_sql=str(args.get("verification_sql") or "").strip(),
                )
                proposals.append(p)
                content = (f"Recorded '{p.name}' for verification. If you have "
                           f"nothing else to add for this defect class, stop now.")
                if verbose:
                    print(f"    + proposed: {p.name}")
            elif tc.name == "profile_column":
                try:
                    content = profile.profile_column(
                        str(args.get("table", "")), str(args.get("column", "")),
                        cfg).format_for_model()
                except Exception as e:
                    content = f"profile_column failed: {e}"
            elif tc.name == "run_probe":
                sql = str(args.get("sql") or "")
                content = (_fmt_rows(run_query(sql, cfg)) if sql
                           else "run_probe requires a `sql` argument.")
            else:
                content = f"Unknown tool {tc.name!r}."

            # Budget pressure, stated as a fact rather than an instruction --
            # round 1 measured a 9B treating imperative tool output as the task.
            left = max_steps - step - 1
            if left <= 2:
                content += f"\n\n[{left} step(s) remain in this audit.]"

            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "name": tc.name, "content": content})

    return proposals, usage


def discover(llm: LLM, cfg: DbConfig | None = None,
             classes: list[str] | None = None,
             max_steps: int | None = None,
             verbose: bool = True) -> memory.Artifact:
    """Run the full question-blind audit and return a verified artifact."""
    cfg = cfg or DbConfig()
    max_steps = max_steps or int(os.getenv("DISCOVER_MAX_STEPS", "8"))
    wanted = [_BY_ID[c] for c in (classes or list(_BY_ID)) if c in _BY_ID]

    # Forced, not inherited. If the operator's .env happens to say `curated`,
    # profile_column would hand this audit the hardcoded staleness answer and
    # the whole exercise would be theatre. The discovery agent's only two data
    # paths are run_query (no notes attached) and profile_column (generic
    # checks only, once this gate is shut), so nothing hand-written reaches it.
    os.environ["HARNESS_KNOWLEDGE"] = "discovered"

    print("Building schema card and join graph ...", flush=True)
    jg = joins.infer_joins(cfg)
    card = schema_card.render(cfg, inferred_joins=joins.as_pairs(jg))

    art = memory.Artifact(
        dataset_version=memory.dataset_version(),
        db_fingerprint=memory.db_fingerprint(cfg),
        model=getattr(llm, "model", getattr(llm, "name", "?")),
        discovered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        taxonomy_version=TAXONOMY_VERSION,
    )

    totals = {"proposed": 0, "verified": 0, "rejected": 0, "duplicate": 0}
    usage_all: dict[str, int] = {}
    rejections: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    t0 = time.time()

    for dc in wanted:
        print(f"\n[{dc.id}] {dc.title}", flush=True)
        props, usage = _run_class(llm, dc, card, cfg, max_steps, verbose)
        for k, v in usage.items():
            usage_all[k] = usage_all.get(k, 0) + v

        for p in props:
            totals["proposed"] += 1
            key = (p.name.lower(), *sorted(c.lower() for c in p.columns))
            if key in seen:
                totals["duplicate"] += 1
                print(f"    = duplicate, skipped: {p.name}")
                continue
            seen.add(key)

            v = verify.verify_claim(p.verification_sql, p.columns, cfg)
            claim = memory.Claim(
                id=art.next_id(), name=p.name, finding=p.finding,
                columns=p.columns, guidance=p.guidance,
                verification_sql=p.verification_sql, evidence=v.evidence,
                status="verified" if v.ok else "rejected",
                reason=v.reason, flags=v.flag_list(), taxonomy=dc.id,
            )
            art.claims.append(claim)

            if v.ok:
                totals["verified"] += 1
                extra = "  [FLAGGED]" if claim.flags else ""
                print(f"    VERIFIED {claim.id} {p.name} "
                      f"(evidence={v.evidence:.0f}){extra}")
            else:
                totals["rejected"] += 1
                rejections.append({"claim": p.name, "reason": v.reason,
                                   "sql": p.verification_sql})
                print(f"    REJECTED {claim.id} {p.name} -- {v.reason}")

    art.stats = {
        **totals,
        "classes_run": [d.id for d in wanted],
        "max_steps_per_class": max_steps,
        "elapsed_s": round(time.time() - t0, 1),
        "usage": usage_all,
        "rejections": rejections,
    }
    return art


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    ap = argparse.ArgumentParser(description="Question-blind schema discovery.")
    ap.add_argument("--classes", type=str, default=None,
                    help=f"Comma-separated subset of: {', '.join(_BY_ID)}")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the artifact instead of writing it.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from harness.llm import from_env
    llm = from_env()
    if hasattr(llm, "health"):
        ok, msg = llm.health()
        print(f"LLM: {msg}")
        if not ok:
            return 1

    classes = ([s.strip() for s in args.classes.split(",")] if args.classes else None)
    art = discover(llm, DbConfig(), classes, args.max_steps, not args.quiet)

    s = art.stats
    print(f"\n{'=' * 64}")
    print(f"proposed {s['proposed']}  verified {s['verified']}  "
          f"rejected {s['rejected']}  duplicate {s['duplicate']}   "
          f"({s['elapsed_s']}s, {s['usage'].get('total_tokens', 0):,} tokens)")

    if args.dry_run:
        for c in art.claims:
            print(f"\n{c.id} [{c.status}] {c.name} -- {c.finding}")
        return 0

    path = memory.save(art)
    print(f"-> {path.relative_to(ROOT)}")
    print("Review it with:  python -m harness.review --list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
