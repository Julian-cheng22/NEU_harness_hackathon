"""
The experiment: same model, same questions, same database, harness on vs off.

    .venv\\Scripts\\python eval/run.py                     # both arms
    .venv\\Scripts\\python eval/run.py --arm harness       # one arm
    .venv\\Scripts\\python eval/run.py --limit 5           # smoke run
    .venv\\Scripts\\python eval/run.py --dry-run           # no LLM needed

--dry-run executes only the gold queries and reports what a PERFECT model would
score. It needs no LLM at all, which makes it the right way to prove the
harness plumbing works before committing to a model. If --dry-run is not 100%,
the bug is in the eval, not in the model.

Results are written to eval/out/<arm>-<provider>.json so report.py can render
them later without re-running anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from eval.grade import explain_mismatch, matches  # noqa: E402
from harness import agent, schema_card, dataset  # noqa: E402
from harness.db import DbConfig, connect  # noqa: E402
from harness.memory import knowledge_mode  # noqa: E402

OUT_DIR = dataset.results_dir()


def load_questions(limit: int | None = None, only: str | None = None) -> list[dict]:
    spec = yaml.safe_load((dataset.data_dir() / "questions.yaml").read_text(encoding="utf-8"))
    qs = spec["questions"]
    if only:
        wanted = {s.strip().upper() for s in only.split(",")}
        qs = [q for q in qs
              if q["id"].upper() in wanted
              or set(q.get("defect_ids") or []) & wanted]
    return qs[:limit] if limit else qs


def gold_rows(sql: str, cfg: DbConfig) -> list[dict]:
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def run_arm(arm: str, questions: list[dict], llm, cfg: DbConfig,
            admin: DbConfig, verbose: bool = False) -> list[dict]:
    """Run one arm over every question. Never lets one failure kill the run --
    a crash on question 7 must not discard the six results already earned."""
    records: list[dict] = []
    # Shared so the schema card and join graph are computed once, not 31 times.
    toolbox = agent.make_toolbox(cfg) if arm == "harness" else None
    ddl = schema_card.render_raw_ddl(cfg) if arm == "baseline" else None

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        t0 = time.time()
        try:
            gold = gold_rows(q["gold_sql"], admin)
        except Exception as e:
            records.append({"id": qid, "arm": arm, "correct": False,
                            "error": f"GOLD QUERY FAILED: {e}",
                            "defect_ids": q.get("defect_ids") or []})
            continue

        if arm == "dry":
            res = agent.AgentResult(question=q["question"],
                                    final_sql=q["gold_sql"], rows=gold)
        elif arm == "baseline":
            res = agent.run_baseline(llm, q["question"], cfg, ddl=ddl)
        else:
            res = agent.run_harness(llm, q["question"], cfg, toolbox=toolbox)

        correct = bool(res.rows) and matches(gold, res.rows)
        rec = {
            "id": qid,
            "arm": arm,
            "question": q["question"],
            "defect_ids": q.get("defect_ids") or [],
            "correct": correct,
            "steps": res.steps,
            "tool_calls": res.tool_calls,
            "final_sql": res.final_sql,
            "error": res.error,
            "mismatch": None if correct else explain_mismatch(gold, res.rows),
            "elapsed_s": round(time.time() - t0, 2),
            "usage": res.usage,
        }
        records.append(rec)

        mark = "PASS" if correct else "FAIL"
        tags = ",".join(rec["defect_ids"]) or "control"
        line = f"  [{i:>2}/{len(questions)}] {mark} {qid:<5} [{tags:<8}] {rec['elapsed_s']:>5.1f}s"
        if not correct:
            line += f"  <- {rec['error'] or rec['mismatch']}"
        print(line, flush=True)
        if verbose and rec["final_sql"]:
            print(f"        sql: {rec['final_sql'][:150]}")

    return records


def summarize(records: list[dict]) -> dict:
    total = len(records)
    correct = sum(1 for r in records if r["correct"])
    by_defect: dict[str, dict[str, int]] = {}
    for r in records:
        tags = r["defect_ids"] or ["control"]
        for t in tags:
            d = by_defect.setdefault(t, {"n": 0, "correct": 0})
            d["n"] += 1
            d["correct"] += 1 if r["correct"] else 0
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "by_defect": by_defect,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", choices=["baseline", "harness", "both", "dry"],
                    default="both")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", type=str, default=None,
                    help="Comma-separated question ids or defect ids, e.g. D5,Q01")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--tag", type=str, default=None,
                    help="Suffix for the output filename. Needed to run the "
                         "harness arm twice under different HARNESS_KNOWLEDGE "
                         "modes without one overwriting the other.")
    args = ap.parse_args()

    cfg, admin = DbConfig(), DbConfig.admin()
    questions = load_questions(args.limit, args.only)
    if not questions:
        print("No questions matched.", file=sys.stderr)
        return 1

    provider = "gold" if args.arm == "dry" else os.getenv("HARNESS_LLM", "local")
    llm = None
    if args.arm != "dry":
        from harness.llm import from_env
        try:
            llm = from_env()
        except Exception as e:
            print(f"Could not initialise the LLM ({e}).\n"
                  f"Run with --dry-run equivalent: --arm dry", file=sys.stderr)
            return 1
        if hasattr(llm, "health"):
            ok, msg = llm.health()
            print(f"LLM: {msg}")
            if not ok:
                print("Refusing to start: the model is unreachable. Start "
                      "llama-server, or set HARNESS_LLM=anthropic.", file=sys.stderr)
                return 1

    arms = ["baseline", "harness"] if args.arm == "both" else [args.arm]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, dict] = {}

    for arm in arms:
        km = f", knowledge={knowledge_mode()}" if arm == "harness" else ""
        print(f"\n=== {arm.upper()} ({provider}{km}) -- {len(questions)} questions ===")
        t0 = time.time()
        records = run_arm(arm, questions, llm, cfg, admin, args.verbose)
        s = summarize(records)
        s["elapsed_s"] = round(time.time() - t0, 1)
        summaries[arm] = s

        path = OUT_DIR / f"{arm}-{provider}{'-' + args.tag if args.tag else ''}.json"
        path.write_text(json.dumps(
            {"arm": arm, "provider": provider, "dataset": dataset.name(),
             # Recorded because the harness arm's result is meaningless without
             # it: the same code scores differently depending on whether its
             # business knowledge was hand-written or discovered.
             "knowledge": knowledge_mode(),
             "summary": s, "records": records},
            indent=2, default=str), encoding="utf-8")
        print(f"  -> {s['correct']}/{s['total']} = {s['accuracy']*100:.1f}%  "
              f"({s['elapsed_s']}s)   written to {path.relative_to(ROOT)}")

    if len(summaries) == 2:
        b, h = summaries["baseline"]["accuracy"], summaries["harness"]["accuracy"]
        print(f"\nLIFT: {b*100:.1f}% -> {h*100:.1f}%  "
              f"({(h-b)*100:+.1f} points)")
        print("\nRender the per-defect breakdown with:  python eval/report.py")
    return 1 if args.arm == "dry" and any(s["correct"] != s["total"] for s in summaries.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
