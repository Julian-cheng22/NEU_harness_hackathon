"""
Verify web/static/data.js still matches the source data it was baked from.

    python scripts/check_dashboard_data.py

`web/static/data.js` is generated (`python -m web.build_data`) but committed, because
the dashboard must render from the file system with no server. That combination
drifts silently: re-run `eval/run.py`, forget to re-bake, and the page shows
yesterday's numbers while looking perfectly healthy. The README warns about this
twice. This script is that warning made enforceable, so a stale file fails CI
instead of going up on a public URL.

Deliberately standalone. `web/build_data.py` imports `web/server.py`, which imports
the whole model stack -- pymysql, sqlglot, google-genai, anthropic, fastapi -- none
of which the dashboard itself needs. Publishing the page must not depend on any of
that, so this reads the generated file directly and needs only PyYAML.

Exit 0 = current. Exit 1 = run `python -m web.build_data` and commit the result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_JS = ROOT / "web" / "static" / "data.js"
OUT_DIR = ROOT / "eval" / "out"


def baked_payload() -> dict[str, Any]:
    """The JSON object out of `window.__HARNESS_DATA__ = {...};`."""
    text = DATA_JS.read_text(encoding="utf-8")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise SystemExit(f"{DATA_JS.name} contains no JSON object -- "
                         f"run `python -m web.build_data`")
    return json.loads(text[start:end + 1])


def _ids(path: Path, key: str) -> list[str]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [str(x["id"]) for x in spec[key]]


def main() -> int:
    if not DATA_JS.exists():
        print("MISSING web/static/data.js -- run `python -m web.build_data`")
        return 1

    payload = baked_payload()
    problems: list[str] = []
    notes: list[str] = []

    # --- questions and defects -------------------------------------------------
    # Compare ids, not counts: a rename that keeps the count would otherwise pass.
    for key in ("questions", "defects"):
        want = _ids(ROOT / "data" / f"{key}.yaml", key)
        got = [str(x.get("id")) for x in payload.get(key, [])]
        if want != got:
            missing, extra = set(want) - set(got), set(got) - set(want)
            detail = f"{len(got)} baked vs {len(want)} in data/{key}.yaml"
            if missing:
                detail += f"; missing {sorted(missing)}"
            if extra:
                detail += f"; stale {sorted(extra)}"
            problems.append(f"{key}: {detail}")

    # --- eval results ----------------------------------------------------------
    baked_runs = {r.get("file"): r for r in payload.get("runs", [])}
    for p in sorted(OUT_DIR.glob("*.json")):
        baked = baked_runs.pop(p.name, None)
        try:
            live = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            notes.append(f"{p.name}: unreadable, skipped ({type(e).__name__})")
            continue

        if baked is None:
            problems.append(f"{p.name}: present in eval/out but not baked into data.js")
            continue

        for field in ("arm", "provider"):
            if baked.get(field) != live.get(field):
                problems.append(f"{p.name}: {field} is {baked.get(field)!r} baked, "
                                f"{live.get(field)!r} on disk")

        b = baked.get("summary") or {}
        live_s = live.get("summary") or {}
        if (b.get("correct"), b.get("total")) != (live_s.get("correct"), live_s.get("total")):
            problems.append(f"{p.name}: baked {b.get('correct')}/{b.get('total')}, "
                            f"on disk {live_s.get('correct')}/{live_s.get('total')}")

    # Anything still in baked_runs came from a file that is not in this checkout.
    # Benign and expected: eval/out is mostly gitignored, so a local-only run
    # (a dry run, an aborted slice) legitimately survives only inside data.js.
    for name in baked_runs:
        notes.append(f"{name}: baked into data.js, not in this checkout (local-only run)")

    for n in notes:
        print(f"  note: {n}")

    if problems:
        print("\nweb/static/data.js is STALE:")
        for p_ in problems:
            print(f"  - {p_}")
        print("\nFix: python -m web.build_data   (then commit web/static/data.js)")
        return 1

    runs = len(payload.get("runs", []))
    print(f"OK  data.js is current -- {len(payload.get('questions', []))} questions, "
          f"{len(payload.get('defects', []))} defects, {runs} run"
          f"{'' if runs == 1 else 's'}, snapshot {payload.get('generated_at', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
