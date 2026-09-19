"""
Render the eval results.

    .venv\\Scripts\\python eval/report.py
    .venv\\Scripts\\python eval/report.py --provider gemini

The per-defect table is the most persuasive artifact this project produces.
A single aggregate number ("62% -> 84%") invites the question "on what?".
The breakdown answers it: it shows WHICH kind of mess the harness fixes, and
just as importantly which it does not. Report both.

The control row is not decoration. If the harness scores WORSE on controls,
the tools are costing more context than they earn, and that belongs in the
writeup rather than hidden.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from harness import dataset
OUT_DIR = dataset.results_dir()

DEFECT_ORDER = ([f"D{i}" for i in range(1, 11)] if dataset.name() == "saas" else [f"MT{i:02d}" for i in range(1, 9)]) + ["control"]


def _load(arm: str, provider: str) -> dict | None:
    p = OUT_DIR / f"{arm}-{provider}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _titles() -> dict[str, str]:
    spec = yaml.safe_load((dataset.data_dir() / "defects.yaml").read_text(encoding="utf-8"))
    out = {d["id"]: d["title"] for d in spec["defects"]}
    out["control"] = "(control -- no defect involved)"
    return out


def _pct(c: int, n: int) -> str:
    return f"{c}/{n}" + (f" ({c / n * 100:>3.0f}%)" if n else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=os.getenv("HARNESS_LLM", "local"))
    args = ap.parse_args()

    console = Console()
    base = _load("baseline", args.provider)
    harn = _load("harness", args.provider)
    dry = _load("dry", "gold")

    if not base and not harn and not dry:
        console.print(f"[red]No results for provider {args.provider!r} in "
                      f"{OUT_DIR.relative_to(ROOT)}.[/red]")
        console.print("Run:  python eval/run.py --arm dry")
        return 1

    if dry and not (base or harn):
        s = dry["summary"]
        console.print(f"\n[bold]Dry run (gold queries only)[/bold] -- "
                      f"{_pct(s['correct'], s['total'])}")
        if s["accuracy"] < 1.0:
            console.print("[red]A dry run below 100% means the EVAL is broken, "
                          "not the model. Fix this before running any arm.[/red]")
        else:
            console.print("[green]Eval plumbing verified: gold queries grade as "
                          "100%. Any shortfall from here is the model.[/green]")
        return 0

    titles = _titles()
    table = Table(title=f"Per-defect accuracy -- {args.provider}",
                  header_style="bold", show_lines=False)
    table.add_column("Defect", style="cyan", no_wrap=True)
    table.add_column("Baseline", justify="right")
    table.add_column("Harness", justify="right")
    table.add_column("Lift", justify="right")
    table.add_column("What the defect is")

    bd = (base or {}).get("summary", {}).get("by_defect", {})
    hd = (harn or {}).get("summary", {}).get("by_defect", {})

    for did in DEFECT_ORDER:
        b, h = bd.get(did), hd.get(did)
        if not b and not h:
            continue
        bn, bc = (b or {}).get("n", 0), (b or {}).get("correct", 0)
        hn, hc = (h or {}).get("n", 0), (h or {}).get("correct", 0)
        lift = ""
        if bn and hn:
            delta = (hc / hn - bc / bn) * 100
            colour = "green" if delta > 0 else ("red" if delta < 0 else "dim")
            lift = f"[{colour}]{delta:+.0f} pts[/{colour}]"
        style = "yellow" if did == "control" else None
        table.add_row(did, _pct(bc, bn), _pct(hc, hn), lift,
                      titles.get(did, ""), style=style)

    console.print()
    console.print(table)

    if base and harn:
        bs, hs = base["summary"], harn["summary"]
        delta = (hs["accuracy"] - bs["accuracy"]) * 100
        console.print(
            f"\n[bold]Overall:[/bold] baseline {_pct(bs['correct'], bs['total'])}"
            f"  ->  harness {_pct(hs['correct'], hs['total'])}"
            f"   [{'green' if delta > 0 else 'red'}]{delta:+.1f} points[/]")

        ctrl_b = bd.get("control", {})
        ctrl_h = hd.get("control", {})
        if ctrl_b.get("n") and ctrl_h.get("n"):
            if ctrl_h["correct"] < ctrl_b["correct"]:
                console.print(
                    "[yellow]WARNING: the harness scored WORSE on control "
                    "questions. The tool output is likely crowding the model's "
                    "context on questions that never needed it. Report this.[/yellow]")

    # Where the harness still fails is the most useful thing for the next iteration.
    if harn:
        fails = [r for r in harn["records"] if not r["correct"]]
        if fails:
            console.print(f"\n[bold]Harness failures ({len(fails)}):[/bold]")
            for r in fails[:12]:
                tags = ",".join(r["defect_ids"]) or "control"
                console.print(f"  {r['id']:<5} [{tags:<8}] "
                              f"{(r['error'] or r['mismatch'] or '')[:90]}")

    if harn and harn["records"]:
        steps = [r["steps"] for r in harn["records"] if r.get("steps")]
        secs = [r["elapsed_s"] for r in harn["records"] if r.get("elapsed_s")]
        tools: dict[str, int] = {}
        for r in harn["records"]:
            for t in r.get("tool_calls") or []:
                tools[t] = tools.get(t, 0) + 1
        if steps:
            console.print(f"\nmedian steps {sorted(steps)[len(steps) // 2]}, "
                          f"median {sorted(secs)[len(secs) // 2]:.1f}s/question")
        if tools:
            console.print("tool use: " + ", ".join(
                f"{k}={v}" for k, v in sorted(tools.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
