# Demo runbook — starting the web dashboard

Everything you need to put the result on a projector, in the order you need it.
Commands are PowerShell (the Windows demo host); on a Mac swap
`.venv\Scripts\python` for `.venv/bin/python`.

**The 30-second version:**

```powershell
.venv\Scripts\python -m web.build_data     # bake the latest results into the page
.venv\Scripts\python -m web.server         # -> http://127.0.0.1:8000
```

If that is all you do, you still have the whole story: the dashboard renders the
committed eval results and needs no database, no model and no network.

---

## Three tiers of readiness

Pick the highest one you can get working **before** you are standing up. Each
tier is a superset of the one above it, and each degrades to the one above it on
its own, with a banner that says why.

| Tier | Command | Needs | What the room sees |
|---|---|---|---|
| **0 — offline** | double-click `web/static/index.html` | nothing at all | Headline lift, per-defect chart, all 31 questions with SQL. Live panel greyed out. |
| **1 — served** | `python -m web.server` | Python venv | Same, plus a live panel that tells you exactly which piece is missing |
| **2 — live A/B** | + Docker + `llama-server` | GPU model + MySQL container | Everything, plus asking the database a new question on stage |

Tier 0 is the insurance policy. The numbers were already earned; they must not
depend on anything being up. **Do not skip `build_data`** — without it the
offline copy quietly shows an older run.

---

## Tier 0 — no server at all

```powershell
.venv\Scripts\python -m web.build_data
start web\static\index.html
```

`build_data` writes `web/static/data.js`; the page reads its numbers from that
global. (A browser cannot `fetch()` a sibling JSON file from a `file://`
origin — CORS blocks it — but a `<script src>` tag is fine, which is why the
data arrives as JS.)

The live panel shows **"no server"** and explains how to start one. That is the
designed behaviour, not a failure.

---

## Tier 1 — the dashboard server

```powershell
.venv\Scripts\python -m web.server              # http://127.0.0.1:8000
.venv\Scripts\python -m web.server --port 8011  # if 8000 is taken
```

Leave it running in its own terminal. The page now fetches `/api/results` live,
so a re-run of `eval/run.py` shows up on refresh without re-baking `data.js`.

Bind to localhost. `--host 0.0.0.0` exists for demoing off a second laptop and
prints a warning when used — this server runs model-written SQL and has no
authentication.

---

## Tier 2 — the live A/B panel

Two more things have to be up. Start them in this order, because the database
takes the longest to become healthy.

**1. Database**

```powershell
docker compose up -d
docker compose ps        # wait for harness_mysql (healthy)
```

First run on a fresh volume also needs the seed and the read-only account:

```powershell
# PowerShell has no `<` redirection -- pipe the file in instead
Get-Content data\seed.sql -Raw | docker compose exec -T mysql mysql -uroot -phackathon harness
docker compose exec -T mysql mysql -uroot -phackathon -e "CREATE USER IF NOT EXISTS 'harness_ro'@'%' IDENTIFIED BY 'readonly'; GRANT SELECT ON harness.* TO 'harness_ro'@'%'; FLUSH PRIVILEGES;"
```

**2. Model**

```powershell
.\scripts\setup-local-model.ps1     # downloads if needed, starts llama-server on :8080
```

**3. Confirm both, then start the server**

```powershell
.venv\Scripts\python -m harness.llm
.venv\Scripts\python -m web.server
```

The status pill top-right of the live panel should read **`qwen3.5-9b · db ok`**
in green. Anything else and the panel tells you which half is down and pastes
the actual error — read it, it is specific.

---

## Pre-flight, ten minutes before you present

Copy-paste this block. Every line should look like the comment next to it.

```powershell
docker compose ps                            # harness_mysql   Up (healthy)
.venv\Scripts\python -m harness.llm          # OK  llama.cpp up; models=['qwen3.5-9b']
.venv\Scripts\python eval\run.py --arm dry   # 31/31 = 100.0%
.venv\Scripts\python -m web.build_data       # wrote web\static\data.js
.venv\Scripts\python -m web.server
```

Then open `http://127.0.0.1:8000` and **run one live question before the room
arrives** — the first question of the session pays a ~15 s one-off cost to build
the schema card and the join graph. Pay it in private.

`--arm dry` runs only the gold SQL, so it is a test of the eval, not of the
model. If it is not 100%, fix that before you trust any other number on screen.

---

## The walkthrough

Five minutes, top to bottom. The page is already in this order.

1. **Headline** — "Same model, same questions, same database. The only variable
   is the harness." Point at `+25.8 pts`.
2. **Accuracy by defect** — the answer to "on what, exactly?". Click a bar to
   filter the question list beneath it. Read the control-row footnote out loud;
   it is the honesty check, and volunteering it is worth more than being asked.
3. **Questions** — click one. Gold SQL, what each arm actually wrote, and the
   harness tool trace. `Q11`, `Q13` or `Q23` make the point fastest.
4. **Ask it live** — pick `Q11 [D5]` from the dropdown and hit **Run both arms**.
   Baseline answers in ~1 s and is wrong; the harness spends ~10 s calling tools
   and is right. That pause is the demo — narrate the tool calls as they stream.

**Live picks, in order of preference** (baseline wrong → harness right, measured):

| Question | Defect | Harness time | The trap |
|---|---|---|---|
| `Q11` | D5 | ~10 s | `MAX()` on a VARCHAR compares *lexically*, so `'N/A'` beats `'400'` |
| `Q23` | D10 | ~6 s | `invoices.amount` mixes cents and dollars — off by 100× |
| `Q08` | D3 | ~3 s | `plan_tier` is a stale cache; the fast answer is the wrong one |

Deep link straight to one: `http://127.0.0.1:8000/#ask=Q11`. It scrolls to the
live panel and runs immediately — handy when you do not want to scroll a 31-row
dropdown in front of an audience.

Two gotchas at the keyboard: a **typed question overrides the dropdown**, so
clear the text box before using a preset (otherwise the run is ungraded), and
only **one question runs at a time** — a second click while one is in flight
gets a polite refusal, not a queue.

---

## The numbers on screen

Qwen3.5-9B Q4_K_M, local llama.cpp, 31 questions, both arms at temperature 0.

| | Baseline | Harness |
|---|---|---|
| Accuracy | 64.5% (20/31) | **90.3% (28/31)** |
| Median time per question | 0.9 s | 6.4 s |
| Context | raw DDL, one shot | M-Schema + tools, up to 12 steps |

The lift is **+25.8 points**. The cost is ~7× the latency — say so before
someone works it out; the harness is buying correctness with time, and on a
question a human would otherwise get wrong silently, that is the right trade.

One regression to own if asked: `Q14` (D6, duplicate companies) — baseline right,
harness wrong.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error during connect: ... dockerDesktopLinuxEngine` | Docker Desktop is not running | Start Docker Desktop, then `docker compose up -d` |
| Pill says **database unreachable** | container down, or `DB_PORT` mismatch | `docker compose up -d`; on the Windows host `.env` must say `3307` (native MySQL owns 3306) |
| Pill says **model unreachable** | `llama-server` is not up on :8080 | `.\scripts\setup-local-model.ps1`, then re-check with `python -m harness.llm` |
| Pill says **no server** on `127.0.0.1:8000` | you opened the file from disk, or the server died | Start `python -m web.server` and reload |
| Banner mentions **Gemini is frozen** | a stale `HARNESS_LLM=gemini` in `.env` | Set `HARNESS_LLM=local` (see below) |
| Page loads unstyled / empty | assets blocked or `data.js` missing | Re-run `python -m web.build_data`; hard-reload (Ctrl+F5) |
| Numbers look like yesterday's | `data.js` is stale | Re-run `python -m web.build_data` after **every** `eval/run.py` |
| ~103 tests fail, dry run says 0/31 | the container vanished | `docker compose ps`; `docker compose up -d`. Data survives in the `mysql_data` volume |
| Port 8000 in use | something else has it | `python -m web.server --port 8011` |

---

## Questions the room may ask

**"Is this calling an API?"** No. Every number on screen was produced by
Qwen3.5-9B running locally on an RTX 4060. `review_runtime_20260914/REPORT.md`
records the model hash and serving config; no paid inference was used.

**"What about Gemini?"** The Gemini path was **frozen on 2026-09-18** after a
Google API policy change. `HARNESS_LLM=gemini` now refuses to start and says so;
the adapter is still in `harness/llm.py` and can be thawed with
`GEMINI_UNFREEZE=true` if that changes. Nothing on the dashboard depends on it —
every committed run in `eval/out/` is `local`.

**"Why does the run picker only show one option?"** It lists one entry per
*provider*, and all seven result files in `eval/out/` are `local`. The dashboard
renders the untagged pair (`baseline-local.json` vs `harness-local.json`). The
tagged variants — `-curated`, `-discovered` — are the learned-schema ablation
and are not reachable from the picker; open the JSON if someone wants them.

**"Why sequential, not side by side?"** One `llama-server` on one GPU serialises
the requests anyway. Running the arms concurrently would look better and measure
worse — the latencies would be queueing artefacts.
