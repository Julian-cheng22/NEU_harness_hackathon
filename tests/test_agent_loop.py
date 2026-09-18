"""
End-to-end pipeline tests with a SCRIPTED model.

Everything here is real except the LLM: real database, real tool dispatch, real
SQL execution, real grading. Only the model's replies are canned, replayed from
a fixed script.

That is deliberate. It means the agent loop, tool plumbing, message threading,
repair path and grader are all verified on every test run, for free, with no API
key, no GPU and no network -- so when a real model later gets something wrong,
we know it is the model and not the harness. It also pins the OpenAI-shaped
message contract that LocalLLM (llama.cpp) depends on.

    .venv\\Scripts\\python -m pytest tests/test_agent_loop.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from eval.grade import matches, normalize  # noqa: E402
from harness import agent  # noqa: E402
from harness.db import DbConfig, connect  # noqa: E402
from harness.llm import AnthropicLLM, LLMResponse, ToolCall, strip_thinking  # noqa: E402


class ScriptedLLM:
    """Replays a fixed list of LLMResponses and records what it was sent."""

    name = "scripted"

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.calls: list[list[dict]] = []
        self.tools_seen: list[list[dict]] = []

    def chat(self, messages, tools=None, temperature: float = 0.0) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        self.tools_seen.append(tools or [])
        if not self.script:
            return LLMResponse(text="(script exhausted)")
        return self.script.pop(0)


def _gold(sql: str) -> list[dict]:
    with connect(DbConfig.admin()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Baseline arm
# ---------------------------------------------------------------------------

def test_baseline_extracts_and_runs_fenced_sql():
    llm = ScriptedLLM([LLMResponse(
        text="```sql\nSELECT COUNT(*) AS n FROM employees\n```")])
    res = agent.run_baseline(llm, "How many employees?", ddl="-- ddl --")
    assert res.error is None
    assert res.rows == [{"n": 40}]
    assert res.steps == 1


def test_baseline_recovers_unfenced_sql():
    """Small models forget the fence. Scoring that as a failure would flatter
    the harness arm for a formatting slip rather than a reasoning one."""
    llm = ScriptedLLM([LLMResponse(text="SELECT COUNT(*) AS n FROM employees;")])
    res = agent.run_baseline(llm, "How many employees?", ddl="-- ddl --")
    assert res.rows == [{"n": 40}]


def test_baseline_reports_missing_sql():
    llm = ScriptedLLM([LLMResponse(text="I am not sure how to answer that.")])
    res = agent.run_baseline(llm, "How many employees?", ddl="-- ddl --")
    assert res.error and "No SQL" in res.error
    assert not res.rows


def test_baseline_gets_no_tools():
    """The control arm must be a genuine control."""
    llm = ScriptedLLM([LLMResponse(text="```sql\nSELECT 1 AS n\n```")])
    agent.run_baseline(llm, "anything", ddl="-- ddl --")
    assert llm.tools_seen == [[]] or not llm.tools_seen[0]


# ---------------------------------------------------------------------------
# Harness arm
# ---------------------------------------------------------------------------

def test_harness_dispatches_tools_and_returns_final_rows():
    """The full loop: call a tool, read the result, then run the final query."""
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "resolve_term", {"query": "revenue"})]),
        LLMResponse(tool_calls=[ToolCall("c2", "profile_column",
                                         {"table": "invoices", "column": "amount"})]),
        LLMResponse(tool_calls=[ToolCall("c3", "execute_sql",
                                         {"sql": "SELECT COUNT(*) AS n FROM employees"})]),
        LLMResponse(text="There are 40 employees."),
    ])
    res = agent.run_harness(llm, "How many employees?")
    assert res.error is None
    assert res.rows == [{"n": 40}]
    assert res.tool_calls == ["resolve_term", "profile_column", "execute_sql"]
    assert res.answer_text == "There are 40 employees."


def test_harness_threads_tool_results_back_as_tool_role():
    """Pins the OpenAI message contract that llama.cpp's server expects."""
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "infer_joins", {})]),
        LLMResponse(text="done"),
    ])
    agent.run_harness(llm, "how do tables relate?")
    second = llm.calls[1]
    assistant = [m for m in second if m["role"] == "assistant"]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert assistant and assistant[0]["tool_calls"][0]["function"]["name"] == "infer_joins"
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"
    # The tool actually ran against the DB, not a stub.
    assert "customers.customer_id" in tool_msgs[0]["content"]


def test_harness_repairs_a_failed_query():
    """Error -> read message -> fix. The repair path is the agent loop itself."""
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "execute_sql",
                                         {"sql": "SELECT COUNT(*) AS n FROM employee"})]),
        LLMResponse(tool_calls=[ToolCall("c2", "execute_sql",
                                         {"sql": "SELECT COUNT(*) AS n FROM employees"})]),
        LLMResponse(text="40."),
    ])
    res = agent.run_harness(llm, "How many employees?")
    assert res.rows == [{"n": 40}]
    # The model must have been shown the real MySQL error to repair from.
    failed = [m for m in llm.calls[1] if m["role"] == "tool"][0]["content"]
    assert "QUERY FAILED" in failed and "employee" in failed


def test_harness_only_grades_successful_queries():
    """A failing final query must not overwrite an earlier good result."""
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "execute_sql",
                                         {"sql": "SELECT COUNT(*) AS n FROM employees"})]),
        LLMResponse(tool_calls=[ToolCall("c2", "execute_sql",
                                         {"sql": "SELECT * FROM nope"})]),
        LLMResponse(text="done"),
    ])
    res = agent.run_harness(llm, "How many employees?")
    assert res.rows == [{"n": 40}]


def test_harness_refuses_mutations_through_the_tool():
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "execute_sql",
                                         {"sql": "DROP TABLE churn_log"})]),
        LLMResponse(text="I cannot do that."),
    ])
    res = agent.run_harness(llm, "delete the churn log")
    assert not res.rows
    refusal = [m for m in llm.calls[1] if m["role"] == "tool"][0]["content"]
    assert "Only SELECT" in refusal
    assert _gold("SELECT COUNT(*) AS n FROM churn_log")[0]["n"] == 49   # still there


def test_harness_survives_a_bad_tool_name():
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "nonexistent_tool", {})]),
        LLMResponse(tool_calls=[ToolCall("c2", "execute_sql",
                                         {"sql": "SELECT COUNT(*) AS n FROM employees"})]),
        LLMResponse(text="40"),
    ])
    res = agent.run_harness(llm, "How many employees?")
    assert res.rows == [{"n": 40}]


def test_harness_respects_the_step_limit():
    """A model that loops forever must terminate, not hang the eval."""
    llm = ScriptedLLM([LLMResponse(tool_calls=[ToolCall(f"c{i}", "infer_joins", {})])
                       for i in range(10)])
    res = agent.run_harness(llm, "loop forever", max_steps=3)
    assert res.steps == 3
    assert res.error and "step limit" in res.error


def test_harness_schema_card_is_in_the_system_prompt():
    llm = ScriptedLLM([LLMResponse(text="done")])
    agent.run_harness(llm, "anything")
    system = llm.calls[0][0]["content"]
    assert "[DB_ID]" in system and "[Foreign keys]" in system
    assert "deals.acct_id = customers.customer_id" in system


# ---------------------------------------------------------------------------
# A real question, answered correctly via the loop, graded against gold
# ---------------------------------------------------------------------------

def test_full_pipeline_scores_a_defect_question():
    """Q11 (D5): MAX on a VARCHAR column. The scripted model does what a
    harnessed model should -- profiles the column, then filters before CAST."""
    gold_sql = ("SELECT MAX(CASE WHEN event_value REGEXP '^[0-9]+(\\\\.[0-9]+)?$' "
                "THEN CAST(event_value AS DECIMAL(18,4)) END) AS max_value FROM usage_events")
    gold = _gold(gold_sql)

    good = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "profile_column",
                                         {"table": "usage_events", "column": "event_value"})]),
        LLMResponse(tool_calls=[ToolCall("c2", "execute_sql", {"sql": gold_sql})]),
        LLMResponse(text="The highest usage value is 400."),
    ])
    res = agent.run_harness(good, "What is the highest single usage value recorded?")
    assert matches(gold, res.rows), "correct SQL should grade as a match"

    naive = ScriptedLLM([LLMResponse(
        text="```sql\nSELECT MAX(event_value) AS max_value FROM usage_events\n```")])
    bad = agent.run_baseline(naive, "What is the highest single usage value recorded?",
                             ddl="-- ddl --")
    assert bad.rows and not matches(gold, bad.rows), (
        "the lexical-MAX trap must NOT grade as correct")


# ---------------------------------------------------------------------------
# Grader
# ---------------------------------------------------------------------------

def test_grader_ignores_column_names_and_order():
    assert matches([{"n": 40}], [{"total": 40}])
    assert matches([{"a": 1, "b": "x"}], [{"b": "x", "a": 1}])


def test_grader_ignores_row_order_but_not_multiplicity():
    assert matches([{"n": 1}, {"n": 2}], [{"n": 2}, {"n": 1}])
    assert not matches([{"n": 1}, {"n": 1}], [{"n": 1}])


def test_grader_tolerates_decimal_float_noise():
    from decimal import Decimal
    assert matches([{"v": Decimal("4633998.90")}], [{"v": 4633998.8999999985}])


def test_grader_tolerates_unrounded_money_totals():
    """Regression, measured in the first full eval run.

    Q25's gold is ROUND(SUM(amount / 1.08), 2) -> 10354399.06. The model wrote
    the same expression without the ROUND -> 10354399.0648. Identical answers,
    scored as a mismatch, and the harness took the blame for a grader artefact.
    """
    from decimal import Decimal
    assert matches([{"v": Decimal("10354399.06")}], [{"v": 10354399.0648}])


def test_grader_tolerates_unrounded_small_magnitude_averages():
    """Regression: Q13's gold is ROUND(AVG(...), 4) = 197.513; the model
    returned the unrounded 197.5130171. A first fix used 10 significant digits,
    which rescued the 1e7-magnitude Q25 but still failed this one."""
    from decimal import Decimal
    assert matches([{"v": Decimal("197.5130")}], [{"v": 197.5130171}])


def test_grader_still_separates_genuinely_different_numbers():
    """The tolerance must not be so loose that it hides real errors -- these
    are all actual baseline-vs-gold pairs from the eval. If any of these start
    passing, the tolerance has gone too far and is inflating the harness score."""
    assert not matches([{"v": 1490}], [{"v": 1490.4}])
    assert not matches([{"v": 25}], [{"v": 26}])                    # Q07
    assert not matches([{"v": 25}], [{"v": 28}])                    # Q07, run 2
    assert not matches([{"v": 76}], [{"v": 74}])                    # Q16
    assert not matches([{"v": 1490}], [{"v": 9714}])                # Q12
    assert not matches([{"v": 2030949.00}], [{"v": 11325360.00}])   # Q23
    assert not matches([{"v": 197.513}], [{"v": 185.9762}])         # Q13 baseline
    assert not matches([{"v": 340874.00}], [{"v": 1228508.00}])     # Q10


def test_grader_rejects_extra_columns():
    assert not matches([{"n": 40}], [{"n": 40, "extra": "noise"}])


def test_grader_rejects_empty_result():
    assert not matches([{"n": 40}], [])


# ---------------------------------------------------------------------------
# Anthropic adapter (pure translation -- no API key needed)
# ---------------------------------------------------------------------------

def test_anthropic_tool_conversion():
    out = AnthropicLLM._convert_tools(agent.TOOLS)
    assert {t["name"] for t in out} >= {"get_schema", "execute_sql", "profile_column"}
    for t in out:
        assert "input_schema" in t and "function" not in t


def test_anthropic_message_conversion_extracts_system():
    system, msgs = AnthropicLLM._convert_messages([
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
    ])
    assert system == "SYS"
    assert msgs == [{"role": "user", "content": "hi"}]
    assert all(m["role"] != "system" for m in msgs)


def test_anthropic_message_conversion_tool_roundtrip():
    _, msgs = AnthropicLLM._convert_messages([
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "execute_sql", "arguments": '{"sql": "SELECT 1"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "execute_sql", "content": "OK"},
    ])
    assert msgs[1]["role"] == "assistant"
    block = msgs[1]["content"][0]
    assert block["type"] == "tool_use" and block["input"] == {"sql": "SELECT 1"}
    assert msgs[2]["role"] == "user"                      # NOT role="tool"
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "c1"


def test_anthropic_merges_parallel_tool_results_into_one_message():
    """Splitting parallel tool results across messages silently trains the
    model to stop making parallel calls."""
    _, msgs = AnthropicLLM._convert_messages([
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "b", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "tool", "tool_call_id": "c2", "content": "r2"},
    ])
    results = [m for m in msgs if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(results) == 1, "parallel tool results must share ONE user message"
    assert len(results[0]["content"]) == 2


# ---------------------------------------------------------------------------
# Thinking-mode stripping (the live-demo hazard)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("<think>hmm</think>The answer is 40.", "The answer is 40."),
    ("<think>a</think>\n\n<think>b</think>ok", "ok"),
    ("no thinking here", "no thinking here"),
    ("prefix<think>truncated mid-thought", "prefix"),   # hit the token limit
])
def test_strip_thinking(raw, want):
    assert strip_thinking(raw) == want
