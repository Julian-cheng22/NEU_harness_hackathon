"""
The two arms of the experiment.

  run_baseline(llm, question)  -- raw DDL, one shot, no tools, no repair.
  run_harness(llm, question)   -- M-Schema + tools + repair loop.

Both are graded the same way: by EXECUTING the SQL they produce and comparing
the result set to the gold query's result set. That is why both return a
`final_sql` and `rows`.

A note on fairness, because this is the part a reviewer should attack first:
the baseline is not a strawman. It gets the real schema via SHOW CREATE TABLE
(including every column COMMENT), the full question, and an explicit
instruction to write correct MySQL. It is exactly what you get from pasting
your schema into a chat window, which is the thing we are claiming to beat.
What it does NOT get is tools, value domains, profiling, or a second attempt.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# Optional observer for the web UI. The eval never passes one, so the default
# is a no-op and the measured behaviour of both arms is unchanged -- a callback
# that raises must not be able to fail a run that would otherwise have scored.
EventFn = Callable[[dict[str, Any]], None]


def _emit(on_event: EventFn | None, **payload: Any) -> None:
    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception:
        pass

from . import glossary, joins, profile, schema_card
from .db import DbConfig
from .execute import execute_sql, format_result_for_model
from .llm import LLM

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class AgentResult:
    question: str
    final_sql: str = ""
    rows: list[dict] = field(default_factory=list)
    answer_text: str = ""
    steps: int = 0
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def produced_sql(self) -> bool:
        return bool(self.final_sql)


# ---------------------------------------------------------------------------
# Baseline arm
# ---------------------------------------------------------------------------
_BASELINE_SYSTEM = """You are a data analyst. Write a single MySQL SELECT query \
that answers the user's question against the schema below.

Reply with ONLY the SQL in a ```sql code block. No explanation.

{ddl}
"""


def run_baseline(llm: LLM, question: str, cfg: DbConfig | None = None,
                 ddl: str | None = None, on_event: EventFn | None = None) -> AgentResult:
    """One shot, raw DDL, no tools, no retry."""
    cfg = cfg or DbConfig()
    ddl = ddl if ddl is not None else schema_card.render_raw_ddl(cfg)

    res = AgentResult(question=question)
    _emit(on_event, type="prompting", arm="baseline",
          detail=f"raw DDL ({len(ddl):,} chars), one shot, no tools")
    try:
        resp = llm.chat(
            [{"role": "system", "content": _BASELINE_SYSTEM.format(ddl=ddl)},
             {"role": "user", "content": question}],
            temperature=0.0,
        )
    except Exception as e:
        res.error = f"LLM call failed: {e}"
        return res

    res.steps = 1
    res.answer_text = resp.text
    res.usage = resp.usage
    sql = extract_sql(resp.text)
    if not sql:
        res.error = "No SQL found in the model's reply."
        return res

    res.final_sql = sql
    _emit(on_event, type="sql", arm="baseline", sql=sql)
    out = execute_sql(sql, cfg, skip_explain=True)
    if out["ok"]:
        res.rows = out["rows"]
    else:
        res.error = out["error"]
    return res


# ---------------------------------------------------------------------------
# Harness arm
# ---------------------------------------------------------------------------
_HARNESS_SYSTEM = """You are a data analyst working with a company's internal \
database. The data is messy: column names are inconsistent between tables, some \
columns are stale caches, some numeric data is stored as text, and referential \
integrity is not enforced.

THE ONLY THING THAT COUNTS IS ANSWERING THE EXACT QUESTION ASKED.
The tools below exist to stop you getting that answer wrong. They will report
problems in the data. Those reports are context, NOT new tasks. If a tool warns
about orphaned rows and the question did not ask about orphaned rows, note it
and move on -- do not answer a question nobody asked.

Tools:
  - resolve_term     what a business term means HERE (units, tax, timezones)
  - profile_column   what is really in a column: nulls, junk, stale values
  - infer_joins      how tables relate (no foreign keys are declared)
  - get_schema       the schema with real value domains
  - execute_sql      run a SELECT and see the result

How to work:
  1. If the question involves money, time, usage volume, tiers, churn, or a
     join across tables, call resolve_term FIRST. The conventions here are not
     the obvious ones.
  2. Profile a column before you aggregate or filter on it -- once. Do not
     profile the same column twice.
  3. If execute_sql errors or returns zero rows, read the message, fix the
     query, and retry. Do not report an error as the answer.
  4. Then STOP INVESTIGATING and answer. Call execute_sql one last time with
     your final query, and make that query return EXACTLY what was asked:
       - only the columns asked for -- no helpful extras
       - "which X has the most Y" means ORDER BY ... DESC LIMIT 1
       - "how many rows" is COUNT(*); "how many distinct values" is
         COUNT(DISTINCT col). These are different numbers. Re-read which the
         question wants.
     Then state the answer in one sentence.

You have a limited number of steps. Spend them on the question, not on
exploring the database.

{schema}
"""

# Re-stated after the schema card, because an 8 KB card between the question and
# the model's first token is enough for a 9B to lose the thread. Measured: the
# model answered an entirely different question (an orphan count) on a question
# about deal value by industry.
_QUESTION_ANCHOR = """{question}

Before your final execute_sql, re-read that question and check your query \
answers it exactly -- the right metric, the right filter, the right number of \
rows and columns."""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "get_schema",
        "description": "The database schema with real value domains and inferred joins.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "infer_joins",
        "description": ("How tables actually relate, inferred from value overlap. "
                        "This database declares NO foreign keys. Also reports "
                        "relationships with orphaned rows."),
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "profile_column",
        "description": ("What is really in a column: null rate, full value domain, "
                        "type drift, stale-cache and duplicate-entity warnings."),
        "parameters": {
            "type": "object",
            "properties": {"table": {"type": "string"}, "column": {"type": "string"}},
            "required": ["table", "column"],
        },
    }},
    {"type": "function", "function": {
        "name": "resolve_term",
        "description": ("What a business term means in THIS database -- units, tax "
                        "treatment, timezone conventions, which column is "
                        "authoritative. Call this before writing SQL about money, "
                        "time, usage, tiers or churn."),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }},
    {"type": "function", "function": {
        "name": "execute_sql",
        "description": "Run a single read-only SELECT and return the rows.",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    }},
]


class _ToolBox:
    """Tool dispatch, with the expensive lookups computed once per run.

    infer_joins and the schema card each cost a couple of hundred queries; the
    agent may ask for them repeatedly within one question, and re-running them
    would dominate the eval's wall clock.
    """

    def __init__(self, cfg: DbConfig):
        self.cfg = cfg
        self._joins: list[joins.JoinCandidate] | None = None
        self._card: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_sql: str = ""

    @property
    def joins(self) -> list[joins.JoinCandidate]:
        if self._joins is None:
            self._joins = joins.infer_joins(self.cfg)
        return self._joins

    @property
    def card(self) -> str:
        if self._card is None:
            self._card = schema_card.render(
                self.cfg, inferred_joins=joins.as_pairs(self.joins))
        return self._card

    def reset(self) -> None:
        """Forget the last query, keep the expensive schema/join cache.

        The toolbox is deliberately shared across questions so the schema card
        and join graph are computed once. `last_result` must NOT be shared: an
        agent that fails to execute anything would otherwise be graded on the
        PREVIOUS question's SQL, which reads as a real answer.
        """
        self.last_result, self.last_sql = None, ""

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "get_schema":
                return self.card
            if name == "infer_joins":
                return joins.format_for_model(self.joins)
            if name == "profile_column":
                table, column = args.get("table", ""), args.get("column", "")
                return profile.profile_column(table, column, self.cfg).format_for_model()
            if name == "resolve_term":
                return glossary.format_for_model(
                    glossary.resolve_term(args.get("query", "")))
            if name == "execute_sql":
                sql = args.get("sql", "")
                if not sql:
                    return "execute_sql requires a `sql` argument."
                out = execute_sql(sql, self.cfg)
                # Remember the last SUCCESSFUL query: that is what gets graded.
                if out["ok"]:
                    self.last_result, self.last_sql = out, out.get("sql", sql)
                return format_result_for_model(out)
            return f"Unknown tool {name!r}."
        except Exception as e:
            # A tool crash must reach the model as text, not kill the run.
            return f"Tool {name} failed: {e}"


def run_harness(llm: LLM, question: str, cfg: DbConfig | None = None,
                max_steps: int | None = None,
                toolbox: _ToolBox | None = None,
                on_event: EventFn | None = None) -> AgentResult:
    cfg = cfg or DbConfig()
    max_steps = max_steps or int(os.getenv("AGENT_MAX_STEPS", "12"))
    box = toolbox or _ToolBox(cfg)
    box.reset()

    res = AgentResult(question=question)
    _emit(on_event, type="prompting", arm="harness",
          detail=f"M-Schema card ({len(box.card):,} chars) + {len(TOOLS)} tools, "
                 f"max {max_steps} steps")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _HARNESS_SYSTEM.format(schema=box.card)},
        {"role": "user", "content": _QUESTION_ANCHOR.format(question=question)},
    ]

    # Nudge the model to converge before it burns the whole budget. Two runs hit
    # the step limit (one at 162s) by exploring indefinitely.
    warn_at = max(2, int(max_steps * 0.6))

    for step in range(max_steps):
        res.steps = step + 1
        try:
            resp = llm.chat(messages, tools=TOOLS, temperature=0.0)
        except Exception as e:
            res.error = f"LLM call failed at step {res.steps}: {e}"
            break

        for k, v in (resp.usage or {}).items():
            if isinstance(v, int):
                res.usage[k] = res.usage.get(k, 0) + v

        if not resp.wants_tool:
            res.answer_text = resp.text
            # A model that answers in prose may still have embedded its query.
            if not box.last_sql:
                sql = extract_sql(resp.text)
                if sql:
                    out = execute_sql(sql, cfg)
                    if out["ok"]:
                        box.last_result, box.last_sql = out, sql
            _emit(on_event, type="answer", arm="harness", text=resp.text)
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
            res.tool_calls.append(tc.name)
            _emit(on_event, type="tool_call", arm="harness", step=res.steps,
                  name=tc.name, args=tc.arguments)
            content = box.dispatch(tc.name, tc.arguments)
            # The raw tool output is emitted verbatim; the UI decides how much
            # to show. Truncating here would hide exactly the evidence the
            # demo exists to display.
            _emit(on_event, type="tool_result", arm="harness", step=res.steps,
                  name=tc.name, content=content)
            if res.steps >= warn_at:
                remaining = max_steps - res.steps
                content += (
                    f"\n\n[{remaining} step(s) left. Stop investigating. Run your "
                    f"final query with execute_sql now and answer the question "
                    f"exactly as asked: {question}]"
                )
            messages.append({
                "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                "content": content,
            })
    else:
        res.error = f"Hit the {max_steps}-step limit without a final answer."

    if box.last_result is not None:
        res.final_sql = box.last_sql
        res.rows = box.last_result["rows"]
        _emit(on_event, type="sql", arm="harness", sql=res.final_sql)
    elif not res.error:
        res.error = "Agent never executed a successful query."
    return res


def extract_sql(text: str) -> str:
    """Pull SQL out of a model reply.

    Small models are inconsistent about fencing, so fall back to scanning for a
    bare SELECT before giving up -- otherwise the baseline arm gets scored as a
    failure for a formatting slip rather than a reasoning one, which would
    flatter the harness.
    """
    if not text:
        return ""
    m = _SQL_FENCE.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip().rstrip(";")

    lowered = text.lower()
    for kw in ("select ", "with "):
        i = lowered.find(kw)
        if i != -1:
            candidate = text[i:].strip()
            return candidate.split(";")[0].strip()
    return ""


def make_toolbox(cfg: DbConfig | None = None) -> _ToolBox:
    """Share one toolbox across a whole eval run so the schema card and join
    graph are computed once, not once per question."""
    return _ToolBox(cfg or DbConfig())
