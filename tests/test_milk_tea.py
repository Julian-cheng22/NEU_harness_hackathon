"""Independent expectations, defect discrimination, and business consistency.

Pure checks always run. Set MILK_TEA_TEST_DB=1 for real MySQL integration;
MILK_TEA_DB_NAME defaults to milk_tea. No LLM calls and no API charges.
"""
import os
from collections import Counter, defaultdict
from decimal import Decimal

import pytest
import yaml

from data.milk_tea.generate import COUNTS, HERE, artifacts, build
from eval.grade import matches
from harness import dataset, glossary, memory
from harness.db import DbConfig, assert_read_only, connect

SPEC = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
QUESTIONS = SPEC["questions"]


@pytest.fixture(scope="module")
def generated():
    return build()


def test_frozen_artifacts_reproduce():
    for filename, content in artifacts().items():
        assert (HERE / filename).read_bytes() == content.encode("utf-8"), filename


def test_exact_counts_and_unique_export_ids(generated):
    tables, _, _ = generated
    assert {t:len(rows) for t,rows in tables.items()} == COUNTS
    assert sum(map(len, tables.values())) == 10000
    ids = [r["row_id"] for rows in list(tables.values())[3:] for r in rows]
    assert len(ids) == len(set(ids))


def test_manifest_rows_and_question_coverage(generated):
    tables, _, evidence = generated
    ids = {r["row_id"] for rows in list(tables.values())[3:] for r in rows}
    coverage = Counter(d for q in QUESTIONS for d in q["defect_ids"])
    for d, affected in evidence.items():
        assert 0 < len(affected) < 9950
        assert set(affected) <= ids
        assert len(set(affected)) == len(affected)
        assert coverage[d] >= 2
    assert sum(not q["defect_ids"] for q in QUESTIONS) >= 3
    assert len({q["id"] for q in QUESTIONS}) == len(QUESTIONS)
    assert all("naive_sql" in q for q in QUESTIONS if q["defect_ids"])


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q:q["id"])
def test_frozen_expect_matches_independent_oracle(q, generated):
    _, oracle, _ = generated
    assert Decimal(str(q["expect"])) == oracle[q["id"]]
    assert_read_only(q["gold_sql"])
    if "naive_sql" in q:
        assert_read_only(q["naive_sql"])


def test_replay_payloads_are_identical(generated):
    groups = defaultdict(list)
    for r in generated[0]["stock_in"]:
        groups[r["movement_key"]].append(r)
    assert sum(len(rows)-1 for rows in groups.values()) == 40
    for rows in groups.values():
        assert all({k:v for k,v in r.items() if k != "row_id"} ==
                   {k:v for k,v in rows[0].items() if k != "row_id"} for r in rows)


def test_order_headers_and_returns_reconcile(generated):
    tables = generated[0]
    orders = defaultdict(list)
    for s in tables["sales"]:
        orders[s["order_code"]].append(s)
    for lines in orders.values():
        assert len(lines) == 2
        assert len({s["status"] for s in lines}) == 1
        assert all(s["order_total"] == sum(r["line_tax_amount"] for r in lines) for s in lines)
    sales = {s["row_id"]:s for s in tables["sales"]}
    delivered = {r["sale_line_id"]:r for r in tables["stock_out"] if r["status"] == "posted" and r["source_type"] == "sale"}
    for r in tables["sales_returns"]:
        s = sales[r["sale_line_id"]]
        assert s["status"] == "approved"
        assert abs(r["qty"]) <= s["qty"]
        assert abs(r["refund_net"]) <= s["line_net_amount"]
        assert all(r[k] == s[k] for k in ("material_id", "warehouse_id", "unit", "batch_no"))
        if r["status"] == "C":
            assert delivered[s["row_id"]]["order_date"] <= r["order_date"]


def test_source_mirrors_and_nonnegative_stock(generated):
    tables = generated[0]
    sources = {"production":{r["row_id"]:r for r in tables["production_receipts"]},
               "sales_return":{r["row_id"]:r for r in tables["sales_returns"]}}
    packs = {r["material_id"]:r["units_per_box"] for r in tables["materials"]}
    seen = set()
    events = []
    source_count = Counter()
    for row in tables["stock_in"]:
        if row["movement_key"] in seen:
            continue
        seen.add(row["movement_key"])
        if row["source_type"] in sources:
            origin = sources[row["source_type"]][row["source_line_id"]]
            source_count[(row["source_type"], row["source_line_id"])] += 1
            assert all(row[k] == origin[k] for k in ("material_id", "warehouse_id", "unit", "batch_no", "order_date"))
            assert row["qty"] == abs(origin["qty"])
            assert (row["status"] == "posted") == (origin["status"] in {"completed", "C"})
        if row["status"] == "posted":
            events.append((row["order_date"], 0, row))
    assert len(source_count) == 1550 and set(source_count.values()) == {1}
    events.extend((r["order_date"], 1, r) for r in tables["stock_out"] if r["status"] == "posted")
    balances = defaultdict(Decimal)
    for _, outgoing, r in sorted(events, key=lambda e:(e[0],e[1],e[2]["row_id"])):
        key = (r["warehouse_id"], r["material_id"], r["batch_no"])
        qty = r["qty"] * (packs[r["material_id"]] if r["unit"] == "box" else 1)
        balances[key] += -qty if outgoing else qty
        assert balances[key] >= 0, (key, r)
    assert sum(balances.values()) == generated[1]["MTQ20"]


def test_dataset_and_knowledge_isolation(monkeypatch):
    monkeypatch.setenv("HARNESS_DATASET", "milk_tea")
    monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.delenv("HARNESS_ARTIFACT", raising=False)
    monkeypatch.setenv("HARNESS_KNOWLEDGE", "curated")
    assert DbConfig().database == "milk_tea"
    assert dataset.data_dir() == HERE
    assert dataset.results_dir().name == "milk_tea"
    assert memory.artifact_path().parent == HERE
    assert memory.dataset_version() == "milk-tea-1.0.0"
    assert glossary.resolve_term("refund")[0].name == "refund"
    assert not any("invoices" in t.definition for t in glossary.active_glossary())
    monkeypatch.setenv("HARNESS_KNOWLEDGE", "discovered")
    assert glossary.active_glossary() == []
    monkeypatch.setenv("HARNESS_DATASET", "saas")
    monkeypatch.setenv("HARNESS_KNOWLEDGE", "curated")
    assert glossary.active_glossary() is glossary.GLOSSARY
    assert DbConfig().database == "harness"


@pytest.fixture
def live_cfg(monkeypatch):
    if os.getenv("MILK_TEA_TEST_DB") != "1":
        pytest.skip("Set MILK_TEA_TEST_DB=1 after loading the dedicated MySQL fixture")
    monkeypatch.setenv("HARNESS_DATASET", "milk_tea")
    monkeypatch.setenv("HARNESS_KNOWLEDGE", "curated")
    cfg = DbConfig()
    cfg.database = os.getenv("MILK_TEA_DB_NAME", "milk_tea")
    return cfg


def rows(sql, cfg):
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q:q["id"])
def test_mysql_gold_matches_oracle(q, live_cfg):
    actual = rows(q["gold_sql"], live_cfg)
    assert len(actual) == 1 and len(actual[0]) == 1
    # Strict exact Decimal equality (not the eval grader's relaxed tolerance).
    assert Decimal(str(next(iter(actual[0].values())))) == Decimal(str(q["expect"]))


@pytest.mark.parametrize("q", [q for q in QUESTIONS if "naive_sql" in q], ids=lambda q:q["id"])
def test_mysql_naive_is_silent_wrong_answer(q, live_cfg):
    # Deliberately do not catch errors: each naive query must actually execute.
    assert not matches(rows(q["gold_sql"], live_cfg), rows(q["naive_sql"], live_cfg))


def test_mysql_defects_present(live_cfg):
    spec = yaml.safe_load((HERE / "defects.yaml").read_text(encoding="utf-8"))
    for d in spec["defects"]:
        assert rows(d["evidence_sql"], live_cfg)[0]["evidence"] > 0, d["id"]


def test_mysql_profile_and_guardrails(live_cfg):
    from harness.execute import execute_sql
    from harness.profile import profile_column
    profile = profile_column("stock_out", "weight_text", live_cfg)
    assert any("TYPE DRIFT" in w for w in profile.warnings)
    p = profile_column("stock_in", "posted_at", live_cfg)
    assert any("AMBIGUOUS NULL" in w for w in p.warnings)
    assert not execute_sql("DELETE FROM sales", live_cfg)["ok"]
    grants = rows("SHOW GRANTS FOR CURRENT_USER", live_cfg)
    assert any("SELECT" in str(g) and "milk" in str(g) for g in grants)
    assert not any("ALL PRIVILEGES" in str(g) for g in grants)


def test_mysql_agent_executes_every_gold_through_safety_layer(live_cfg):
    from harness.execute import execute_sql
    for q in QUESTIONS:
        result = execute_sql(q["gold_sql"], live_cfg)
        assert result["ok"], (q["id"], result["error"])
        assert matches(result["rows"], [{"n":q["expect"]}]), q["id"]


def test_mysql_explain_still_blocks_unconstrained_join(live_cfg):
    from harness.execute import execute_sql
    result = execute_sql("SELECT s.row_id, i.row_id FROM sales s CROSS JOIN stock_in i", live_cfg)
    assert not result["ok"] and "unconstrained join" in result["error"]


def test_mysql_loader_refuses_existing_data(live_cfg):
    from data.milk_tea.load import load
    before = rows("SELECT COUNT(*) AS n FROM sales", live_cfg)
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        load(live_cfg.database)
    assert rows("SELECT COUNT(*) AS n FROM sales", live_cfg) == before


def test_mysql_scripted_agent_with_real_tools(live_cfg):
    from harness import agent
    from unittest.mock import Mock
    from harness.llm import LLMResponse, ToolCall
    question = QUESTIONS[12]
    llm = Mock()
    llm.chat.side_effect = [
        LLMResponse(tool_calls=[ToolCall(id="a", name="resolve_term", arguments={"query":"measured weight"})]),
        LLMResponse(tool_calls=[ToolCall(id="b", name="profile_column", arguments={"table":"stock_out", "column":"weight_text"})]),
        LLMResponse(tool_calls=[ToolCall(id="c", name="execute_sql", arguments={"sql":question["gold_sql"]})]),
        LLMResponse(text="The highest valid measured stock-out weight is available in the query result."),
    ]
    result = agent.run_harness(llm, question["question"], cfg=live_cfg)
    assert result.error is None
    assert matches(result.rows, [{"n":question["expect"]}])
    assert result.tool_calls == ["resolve_term", "profile_column", "execute_sql"]
