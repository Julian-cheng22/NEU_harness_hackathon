# NEU Harness Hackathon

A **harness** that helps a small model answer analytical questions correctly over
messy internal company data.

## The thesis

Most text-to-SQL failure on real company data is not a SQL-syntax problem. It is a
**context problem**. The model writes perfectly valid SQL against the schema it was
shown — the schema just never says that `plan_tier` is a stale cache, that
`event_value` is a VARCHAR full of `'N/A'`, or that `acct_id` is the customer key.
So the model is confidently, silently wrong, and nothing errors.

We test that claim by measuring it: **same model, same questions, same database,
harness on vs harness off**, broken down per defect.

---

## Quick start

Requires Docker and Python 3.11+. Nothing else — no MySQL install, no compiler.

```bash
git clone <repo> && cd NEU_harness_hackathon
cp .env.example .env          # macOS/Linux: defaults are fine as-is

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip

docker compose up -d
docker compose exec -T mysql mysql -uroot -phackathon harness < data/seed.sql

# create the read-only account the agent uses
docker compose exec -T mysql mysql -uroot -phackathon -e "
  CREATE USER IF NOT EXISTS 'harness_ro'@'%' IDENTIFIED BY 'readonly';
  GRANT SELECT ON harness.* TO 'harness_ro'@'%'; FLUSH PRIVILEGES;"

.venv/bin/python -m pytest tests/ -q                 # should be all green
```

> **Windows note:** native MySQL already owns port 3306, so `.env` on that machine
> sets `MYSQL_HOST_PORT=3307` and `DB_PORT=3307`. On a Mac leave both at 3306.

### If a hundred tests suddenly fail

Check the container first — this happened during development and the symptom is
alarmingly unspecific:

```bash
docker compose ps        # empty output = the container is gone
docker compose up -d     # the named volume keeps your data
```

If `harness_mysql` has stopped, ~103 tests fail and the eval dry run reports
**0/31** with no mention of the database. The data itself is safe in the
`mysql_data` volume and comes back with the container. Only re-run
`seed.sql` if `SELECT COUNT(*) FROM customers` does not return 128.

You may also need to recreate the read-only user if you ever run
`docker compose down -v` (that *does* delete the volume) — see Quick start.

You do **not** need to be on anyone's network. Everyone runs their own database
from the committed seed. That is deliberate — see *Why not one shared server* below.

---

## The dataset

A synthetic B2B SaaS company: 10 tables, ~28k rows, generated deterministically.

```
customers ──< subscriptions ──< invoices
    │              │
    │              └──< usage_events
    ├──< support_tickets >── employees
    ├──< deals >── campaigns
    └──< churn_log            product_catalog
```

**The flaws in this data are the product.** Every one is deliberate, specified in
[`data/defects.yaml`](data/defects.yaml), injected by
[`data/generate.py`](data/generate.py), and asserted by the test suite:

| id | Defect | Why it is nasty |
|----|--------|-----------------|
| D1 | Customer key spelled 3 ways (`customer_id` / `cust_id` / `acct_id`) | `acct_id` shares no substring with `customer_id`; name matching cannot find it |
| D2 | `status` means different things on 3 tables | Reusing `'won'` on invoices returns 0 rows, which reads as "no data" |
| D3 | `customers.plan_tier` is a stale cache | Disagrees with live tier for 25 accounts; it is one join *shallower*, so it is the tempting choice |
| D4 | `invoices.paid_at IS NULL` means two things | 152 genuinely paid invoices have no timestamp; treating NULL as unpaid overstates receivables |
| D5 | `usage_events.event_value` is VARCHAR with junk | `MAX()` compares **lexically**, so `'N/A'` outranks `'400'`; `AVG()` keeps junk in the denominator. No error either way. (`SUM()` is accidentally safe — 0 adds nothing.) |
| D6 | Same company under two `customer_id`s | Splits that company's revenue roughly in half |
| D7 | `event_ts` is US/Eastern when `source='batch'`, UTC otherwise | No timezone column exists; 20% of events are off by 4 hours |
| D8 | Orphaned `support_tickets.cust_id`, no FK constraints | `INNER JOIN` silently drops them; `COUNT(*)` disagrees and nobody notices |
| D9 | `churn_log.reason_code` runs 1–9, comment documents only 1–4 | The documentation is *confidently incomplete* |
| D10 | `invoices.amount` mixes cents and dollars; `deals.amount` includes tax | Off by 100×, or a fake 8% discrepancy |

### Determinism is a hard requirement

`data/generate.py` uses one seeded RNG, no `datetime.now()`, no set iteration.
The A/B comparison is worthless if the database differs between runs or between
machines, so CI enforces it:

```bash
.venv/bin/python data/generate.py --check    # fails on any drift
```

If you change the generator, **every gold answer in `questions.yaml` must be
re-verified.** Bump `DATASET_VERSION` when you do.

---

## The harness

| Module | What it does | Defects it addresses |
|---|---|---|
| [`schema_card.py`](harness/schema_card.py) | M-Schema rendering with real value domains inline | D2, D9 |
| [`joins.py`](harness/joins.py) | Infers the join graph from **value overlap**, not column names | D1, D8 |
| [`profile.py`](harness/profile.py) | Data-quality probe: type drift, stale caches, ambiguous NULLs, duplicates | D3, D4, D5, D6 |
| [`glossary.py`](harness/glossary.py) | Business-term resolution: units, tax, timezone conventions | D7, D10 |
| [`db.py`](harness/db.py) | Read-only execution with repair-friendly errors | — |

### Two design decisions worth knowing

**M-Schema, not `SHOW CREATE TABLE`.** The XiYanSQL team measured identical models
at 62.13% (M-Schema) vs 57.43% (raw DDL) on BIRD. That is ~5 points from the
*representation alone*, so the baseline arm uses raw DDL and the harness arm uses
M-Schema.

**Join inference uses coverage, not just containment.** The obvious metric — "what
fraction of the child's values exist in this key?" — is broken on small integer
keys, because small ranges trivially contain each other. Measured on this database,
naive containment produced `support_tickets.cust_id -> usage_events.event_id` at
100% and reported it **clean**, which hid D8 completely. We additionally require
*coverage* (what share of the parent's keys are actually referenced). Genuinely
ambiguous cases are reported as `AMBIGUOUS` rather than guessed.

### Honest scoping

`glossary.py` is **curated, not inferred**. The harness does not magically deduce
that `deals.amount` includes tax — nothing in the data reveals that. What it does
is make institutional knowledge *retrievable at the moment the model needs it*
instead of absent. Don't oversell it.

---

## Why not one shared database server

Hackathon WiFi frequently enables AP/client isolation, which silently blocks
peer-to-peer LAN traffic. A demo that depends on one laptop being reachable is a
demo that can die for reasons you cannot debug on stage. So the dataset ships in
git and everyone runs it locally. The Windows host also serves `10.0.0.253:3306`
as a convenience — never as a dependency.

---

## Running the eval

```bash
.venv/bin/python eval/run.py --arm dry     # no LLM needed -- verifies the eval itself
.venv/bin/python eval/run.py --arm both    # baseline vs harness
.venv/bin/python eval/report.py            # per-defect breakdown
```

Start with `--arm dry`. It runs only the gold queries, so a perfect model scores
100%. **If the dry run is not 100%, the bug is in the eval, not the model** —
fix that before trusting any number.

Pick a model with `HARNESS_LLM`:

| Value | Backend | Needs |
|---|---|---|
| `local` | llama.cpp on :8080 (Qwen3.5-9B) | the GGUF + a running `llama-server` |
| `gemini` | Gemini 3 Flash | `GEMINI_API_KEY` |
| `anthropic` | Claude Haiku 4.5 / Sonnet 5 | `ANTHROPIC_API_KEY` |

### Local model

```powershell
.\scripts\setup-local-model.ps1      # downloads + starts llama-server on :8080
```

Measured on the Windows host, 2026-09-12 (RTX 4060 Laptop, 8188 MiB, driver 616.92):

| | |
|---|---|
| Model | `unsloth/Qwen3.5-9B-GGUF` → `Qwen3.5-9B-Q4_K_M.gguf`, 5,680,522,464 bytes |
| Server | llama.cpp **b10934**, `win-cuda-12.4-x64` |
| VRAM | **6996 / 8188 MiB** at `-c 32768` with `q8_0` KV cache — fits, ~1.2 GB spare |
| Load time | ~6 s |
| Decode | **38–39 tok/s** sustained |
| Prompt eval | 3,579-token schema card in ~2.3 s |
| Thinking | disabled via `chat_template_kwargs` — verified no `<think>` in raw output |
| Tool calling | works (`--jinja` is required for this) |

The 38–39 tok/s figure landed at the *dense-equivalent floor*, not above it — the
sparse-MoE architecture did not buy extra decode speed here. Plan accordingly.

---

## The front end

```powershell
.venv\Scripts\python -m web.build_data     # bake eval results into the page
.venv\Scripts\python -m web.server         # http://127.0.0.1:8000
```

One page, two independent halves.

**The dashboard** renders the committed `eval/out/*.json`: the headline lift, the
per-defect breakdown sorted by where the harness earns its keep, and every
question one click from the gold SQL, what each arm actually wrote, and the
harness tool trace. The control row is surfaced on the front page rather than in
a footnote — if the harness ever scores *worse* on defect-free questions, the
page says so in plain language.

**The live panel** runs both arms against the real database and streams the
agent's tool calls over SSE as they happen, baseline first. Picking one of the
31 gold questions grades the result live; a free-text question is clearly marked
ungraded, because there is no gold answer to compare it against.

### It opens without the server

`web/build_data.py` bakes the results into `web/static/data.js`, so
`web/static/index.html` **opens by double-clicking** — no server, no database, no
model, no network. That is deliberate: the measured numbers are already earned
and must not depend on anything being up on demo day. The live panel detects it
has no server and explains how to start one instead of failing silently.

(A browser cannot `fetch()` a sibling JSON file from a `file://` origin — the
origin is opaque, so CORS blocks it. A `<script src>` tag is not blocked, which
is why the data arrives as a JS global rather than a `.json` fetch.)

**Re-run `python -m web.build_data` after every `eval/run.py`**, or the offline
copy will quietly show yesterday's numbers.

### Security note

`web/server.py` binds `127.0.0.1` and has **no authentication**. It executes
model-written SQL and exposes whichever LLM provider is configured. `--host
0.0.0.0` exists for demoing off a second laptop and prints a warning; do not
leave it on a network you do not control. The read-only MySQL grant is still the
layer doing the real enforcing.

---

## Results

Qwen3.5-9B Q4_K_M, local, 31 questions, execution-match grading:

| | Baseline (raw DDL, one shot) | Harness (M-Schema + tools) |
|---|---|---|
| **Overall** | 20/31 — 64.5% | **28/31 — 90.3%** |
| Median latency | 1.0 s/question | 6.0 s/question, median 3 steps |

The per-defect breakdown is the real result. The harness earns its keep on the
defects it was built for and holds everywhere else:

| Defect | Baseline | Harness | |
|---|---|---|---|
| D5 numeric-as-text | 0/3 | **3/3** | +100 |
| D3 stale cache | 1/3 | **3/3** | +67 |
| D7 timezone | 1/3 | **3/3** | +67 |
| D10 units / tax | 1/4 | **3/4** | +50 |
| D1 key naming | 3/3 | 3/3 | — |
| D8 orphan keys | 2/2 | 2/2 | — |
| D9 undocumented enum | 2/2 | 2/2 | — |
| **controls** | 6/6 | **6/6** | — |
| D6 duplicate entities | 2/2 | 1/2 | −50 |

The one regression (D6) is a single question where the agent hit its 12-step
limit rather than a wrong answer.

**Reproducibility.** Three consecutive runs of identical code scored 28/31 every
time, with the *same three* questions failing each run (Q05, Q10, Q14) and no
question flipping between runs. The baseline scored 20/31 in every run, including
after the grader fix — so the tolerance change rescued no baseline answer, and
the lift is not a measurement artefact.

Latency across those runs: median 6.3 s/question, p90 11.2 s, max 61.6 s (the
Q14 step-limit case).

### The three remaining failures

Left unfixed on purpose — they are model limitations, not harness bugs, and
pretending otherwise would mean tuning the harness to the test set:

| | Why it fails |
|---|---|
| Q05 (D2) | Returns three status rows where the question asks for two. The value domain *is* in the schema card; the model over-answers. |
| Q10 (D4+D10) | Needs the status disambiguation **and** the cents/dollars normalisation in one query. `resolve_term` supplies both; the model applies one. |
| Q14 (D6) | Hits the 12-step limit exploring duplicate company names instead of returning the distinct count. |

The baseline's answer to *"what is the highest single usage value recorded?"*
was the string **`'N/A'`** — `MAX()` on a VARCHAR column comparing lexically,
exactly the trap D5 was built to set.

### What went wrong first, and what it taught us

The first full run scored the harness at **61.3%, three points BELOW baseline**.
Three causes, all ours, all now covered by regression tests:

1. **The grader was wrong.** Gold queries apply `ROUND(...)`; the model often
   does not. `10354399.06` vs `10354399.0648` scored as a mismatch. Fixed by
   comparing to 7 significant digits (~1e-7 relative) instead of fixed decimals.
   The fix rescues no baseline answer — every baseline error is far larger.

2. **A tool handed the model the wrong number.** `infer_joins` reported
   *"7 child values have no matching parent"* — distinct values. Asked how many
   **tickets** were orphaned (48 rows), the model reported `7`. D8 went 2/2 →
   0/2 because of our own output. It now reports rows and distinct values
   separately and says which answers a "how many rows" question.

3. **A 9B model treats tool output as instructions.** A warning ending
   *"Prefer LEFT JOIN and account for the unmatched rows"* made the model go do
   that — returning an orphan count for a question about deal value by industry.
   Warnings are now phrased as facts, not imperatives, and the question is
   re-anchored after the 8 KB schema card.

Point 3 is the transferable lesson. Harness design for small models is not only
about surfacing information — it is about **not competing with the user's
question for the model's attention**.

---

## Status

**Verified — 150 tests passing, plus a 31/31 eval dry run:**
- 10-table schema, deterministic generator, all 10 defects asserted present in a live DB
- Read-only enforcement verified at the MySQL grant layer (`DROP` denied)
- Join inference, profiling, schema card, glossary retrieval
- 31 gold questions, every one executed; every `naive_sql` confirmed to produce a *different* answer
- Full agent loop — tool dispatch, message threading, error repair, step limits, grading —
  verified against a **scripted model**, so it runs with no API key, GPU or network

**Not yet verified:** any arm driven by a real model. The agent loop is proven;
what a 9B actually does with it is the open question.

---

## Original project notes

目前的初步想法是让比较强的模型（GPT, Claude）生成特定领域的合成数据，再由我们开发
harness/agent 工具帮助对应的推理模型（Qwen，Gemini flash）来处理这些数据中的问题。
合成数据中需要刻意设置问题例如变量不匹配，数据缺失等等。我们通过开发 harness/agent
来优化这些过程。

1. 大家可以挑自己感兴趣的领域生成对应的数据例如医疗，保险，软件开发，教育等等；
2. 目前推理模型的实现待定。是尝试本地运行小模型如 qwen 还是接 api 运行 Gemini flash；
