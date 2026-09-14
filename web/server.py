"""
Web front end for the harness: results dashboard + live A/B demo.

    .venv\\Scripts\\python -m web.server          # http://127.0.0.1:8000

Two halves, deliberately independent:

  DASHBOARD  reads the committed eval/out/*.json. Needs no database, no model
             and no API key. It also works with this server switched off --
             `python -m web.build_data` bakes the same JSON into
             web/static/data.js so index.html opens straight from the file
             system. That is the demo-day insurance policy: the numbers are
             already earned and must not depend on anything being up.

  LIVE       runs run_baseline and run_harness against the real database and
             the configured model, streaming the agent's tool calls over SSE.
             Needs docker + a model. Degrades to a clear banner when either is
             missing rather than failing silently.

The two arms run SEQUENTIALLY, baseline first. Running them concurrently would
look better and measure worse: one llama-server on one GPU serialises the
requests anyway, so the latencies shown would be queueing artefacts rather than
the numbers we are claiming.

BINDING: 127.0.0.1 by default. This server executes model-written SQL and
exposes the LLM provider; it has no authentication and must not be put on a
network interface. `--host 0.0.0.0` is available for demoing off a second
laptop and prints a warning when used.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from eval.grade import matches  # noqa: E402
from harness import agent, schema_card  # noqa: E402
from harness.db import DbConfig, list_tables  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
OUT_DIR = ROOT / "eval" / "out"

app = FastAPI(title="Harness dashboard", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Dashboard data -- pure file reads, no database
# ---------------------------------------------------------------------------
def _defects() -> list[dict[str, Any]]:
    spec = yaml.safe_load((ROOT / "data" / "defects.yaml").read_text(encoding="utf-8"))
    return [{"id": d["id"], "title": d["title"],
             "naive_failure": (d.get("naive_failure") or "").strip()}
            for d in spec["defects"]]


def _questions() -> list[dict[str, Any]]:
    spec = yaml.safe_load((ROOT / "data" / "questions.yaml").read_text(encoding="utf-8"))
    return [{"id": q["id"], "question": q["question"],
             "defect_ids": q.get("defect_ids") or [],
             "gold_sql": (q.get("gold_sql") or "").strip(),
             "naive_sql": (q.get("naive_sql") or "").strip(),
             "notes": (q.get("notes") or "").strip()}
            for q in spec["questions"]]


def _runs() -> list[dict[str, Any]]:
    """Every eval/out/<arm>-<provider>.json we can find, newest first."""
    out = []
    for p in sorted(OUT_DIR.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        payload["file"] = p.name
        payload["mtime"] = p.stat().st_mtime
        out.append(payload)
    return sorted(out, key=lambda r: -r["mtime"])


def dashboard_payload() -> dict[str, Any]:
    return {"defects": _defects(), "questions": _questions(), "runs": _runs(),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/results")
def api_results() -> dict[str, Any]:
    return dashboard_payload()


# ---------------------------------------------------------------------------
# Health -- what the live half needs, checked separately so the UI can say
# WHICH piece is down instead of "failed to fetch"
# ---------------------------------------------------------------------------
@app.get("/api/health")
def api_health() -> dict[str, Any]:
    provider = os.getenv("HARNESS_LLM", "local").strip().lower()
    db: dict[str, Any] = {"ok": False, "detail": ""}
    try:
        tables = list_tables(DbConfig())
        db = {"ok": bool(tables), "detail": f"{len(tables)} tables as "
                                            f"{DbConfig().user}@{DbConfig().host}:{DbConfig().port}"}
    except Exception as e:
        db = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    llm: dict[str, Any] = {"ok": False, "provider": provider, "detail": ""}
    try:
        from harness.llm import from_env
        client = from_env()
        llm["model"] = getattr(client, "model", provider)
        if hasattr(client, "health"):
            ok, msg = client.health()
            llm.update(ok=ok, detail=msg)
        else:
            # Gemini has no cheap health probe -- a real call costs quota, so
            # report "configured" and let the first question be the test.
            llm.update(ok=True, detail=f"{provider} configured (not probed)")
    except Exception as e:
        llm["detail"] = f"{type(e).__name__}: {e}"

    return {"db": db, "llm": llm, "live_ready": db["ok"] and llm["ok"]}


# ---------------------------------------------------------------------------
# Live A/B -- SSE
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    arms: list[str] = Field(default_factory=lambda: ["baseline", "harness"])
    question_id: str | None = None


class _Runner:
    """Shared, lazily built. The schema card and join graph cost a couple of
    hundred queries; rebuilding them per request would make the first token of
    every question take ~15s."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._toolbox: agent._ToolBox | None = None
        self._ddl: str | None = None
        self._llm: Any = None
        # One question at a time: a single llama-server would serialise these
        # anyway, and two concurrent runs would share the toolbox's last_result.
        self.gate = threading.Semaphore(1)

    def toolbox(self) -> agent._ToolBox:
        with self._lock:
            if self._toolbox is None:
                self._toolbox = agent.make_toolbox(DbConfig())
            return self._toolbox

    def ddl(self) -> str:
        with self._lock:
            if self._ddl is None:
                self._ddl = schema_card.render_raw_ddl(DbConfig())
            return self._ddl

    def llm(self) -> Any:
        with self._lock:
            if self._llm is None:
                from harness.llm import from_env
                self._llm = from_env()
            return self._llm


RUNNER = _Runner()


def _gold_rows(qid: str) -> list[dict] | None:
    """Gold rows for a known question id, so the live panel can grade itself.
    Free-text questions have no gold answer and are shown ungraded."""
    from harness.db import connect
    q = next((x for x in _questions() if x["id"] == qid), None)
    if not q or not q["gold_sql"]:
        return None
    try:
        with connect(DbConfig.admin()) as conn, conn.cursor() as cur:
            cur.execute(q["gold_sql"])
            return list(cur.fetchall())
    except Exception:
        return None


def _jsonable(x: Any) -> Any:
    """Rows come back with Decimal and datetime in them; SSE needs text."""
    return json.loads(json.dumps(x, default=str))


def _run_stream(req: AskRequest) -> Iterator[str]:
    q: queue.Queue[dict | None] = queue.Queue()

    def emit(ev: dict) -> None:
        q.put(ev)

    def work() -> None:
        try:
            gold = _gold_rows(req.question_id) if req.question_id else None
            llm = RUNNER.llm()
            for arm in req.arms:
                emit({"type": "arm_start", "arm": arm})
                t0 = time.time()
                try:
                    if arm == "baseline":
                        res = agent.run_baseline(llm, req.question, DbConfig(),
                                                 ddl=RUNNER.ddl(), on_event=emit)
                    else:
                        res = agent.run_harness(llm, req.question, DbConfig(),
                                                toolbox=RUNNER.toolbox(), on_event=emit)
                except Exception as e:
                    emit({"type": "arm_error", "arm": arm,
                          "error": f"{type(e).__name__}: {e}"})
                    continue

                rows = _jsonable(res.rows)
                correct = None
                if gold is not None:
                    correct = bool(res.rows) and matches(gold, res.rows)
                emit({"type": "arm_done", "arm": arm, "elapsed_s": round(time.time() - t0, 2),
                      "sql": res.final_sql, "rows": rows[:50],
                      "row_count": len(rows), "steps": res.steps,
                      "tool_calls": res.tool_calls, "error": res.error,
                      "answer_text": res.answer_text, "usage": res.usage,
                      "correct": correct})
            emit({"type": "done", "graded": gold is not None})
        except Exception as e:
            emit({"type": "fatal", "error": f"{type(e).__name__}: {e}"})
        finally:
            q.put(None)

    if not RUNNER.gate.acquire(blocking=False):
        yield _sse({"type": "fatal", "error":
                    "Another question is already running. One at a time -- the "
                    "model and the toolbox are shared."})
        return
    try:
        threading.Thread(target=work, daemon=True).start()
        while True:
            try:
                ev = q.get(timeout=30)
            except queue.Empty:
                yield ": keep-alive\n\n"   # stops proxies closing an idle stream
                continue
            if ev is None:
                break
            yield _sse(ev)
    finally:
        RUNNER.gate.release()


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


@app.post("/api/ask")
def api_ask(req: AskRequest) -> StreamingResponse:
    bad = [a for a in req.arms if a not in ("baseline", "harness")]
    if bad:
        raise HTTPException(400, f"Unknown arm(s): {bad}")
    return StreamingResponse(
        _run_stream(req), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------
# Mounted at the ROOT, not /static, and registered last.
#
# index.html references styles.css / app.js / data.js as bare relative paths so
# that double-clicking the file from disk works. Serving it from "/" while the
# assets lived under "/static" made every one of those resolve to "/styles.css"
# and 404 -- an unstyled page with no data. Serving the directory at the root
# makes one set of paths correct in both modes.
#
# Starlette matches routes in registration order, so the /api routes above
# still win; this mount only ever sees what they did not claim.
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost"):
        print(f"WARNING: binding {args.host} exposes an UNAUTHENTICATED endpoint "
              f"that runs model-written SQL. Localhost only unless you mean it.",
              file=sys.stderr)

    import uvicorn
    print(f"\n  Dashboard + live demo:  http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
