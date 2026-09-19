# NEU Harness Hackathon

A **harness** that helps a small model answer analytical questions correctly over
messy internal company data.

**[See the result →](https://usp787.github.io/NEU_harness_hackathon/)** — the measured
baseline-vs-harness comparison, every question one click from its SQL.

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
| `anthropic` | Claude Haiku 4.5 / Sonnet 5 | `ANTHROPIC_API_KEY` |
| ~~`gemini`~~ | **frozen 2026-09-18** — refuses to start | — |

Pre-flight whichever you picked before you need it:

```powershell
.venv\Scripts\python -m harness.llm
```

It validates the credential and confirms the configured model is reachable.
That call spends no generation quota and never prints the key.

### Gemini is frozen

As of **2026-09-18** the Gemini path is out of scope following a Google API
policy change. `HARNESS_LLM=gemini` now raises immediately with an explanation
instead of calling the API, and the web dashboard's health banner says the same
thing in one sentence.

The adapter itself was **not deleted**. `GeminiLLM` in `harness/llm.py` is
intact — the `thought_signature` round-trip and the OpenAI→Gemini
message/tool conversion are the expensive part of that work and we want them
back if the freeze lifts. To thaw it deliberately, set `GEMINI_UNFREEZE=true`
alongside `HARNESS_LLM=gemini` and re-read the current terms first.

No measured result in this repo depends on Gemini: every committed run in
`eval/out/` is `local` (Qwen3.5-9B on the RTX 4060), and
`review_runtime_20260914/` states explicitly that no paid inference was used.

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

> Presenting this? **[`DEMO.md`](DEMO.md)** is the runbook: pre-flight checklist,
> which questions to run live, the numbers to quote, and what to do when a piece
> is down.
>
> Just want to look at the result? It is published at
> **<https://usp787.github.io/NEU_harness_hackathon/>** — no clone, no Docker, no
> model. **[`TRY_IT.md`](TRY_IT.md)** is the reviewer's guide to what is on it,
> including why the live panel is greyed out there.

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

## Earning the glossary instead of writing it

Breaking the lift down by which module earned each point exposes an awkward
fact: **about half of it comes from `glossary.py`, which is hand-written.** D7
(+2) and D10 (+2) are glossary-only. The most technically interesting module,
join inference, earned zero — the baseline already passed D1 and D8. So the
harness's best-measured component is the one that needs a human to have already
written the convention down, and it does not port to another company.

This section is the attempt to replace it with knowledge the model *earns*.

### How it works

```powershell
.venv\Scripts\python -m harness.discover        # audit, verify, write the artifact
.venv\Scripts\python -m harness.review --queue  # what most needs a human
```

[`discover.py`](harness/discover.py) points the model at the database with no
questions in sight and asks, one defect class at a time, "is this kind of
problem here, and can you prove it?" The taxonomy is ten schema-independent
classes — stale copies, type drift, overloaded NULLs, unit inconsistency and so
on. It names no table and no column.

A claim is only kept if it arrives with a proof, and the proof is checked
mechanically by [`verify.py`](harness/verify.py): a single SELECT returning one
row, one column named `evidence`, counting the affected rows. That contract
exists because the model is being asked to produce both the claim and its
evidence, which is an obvious incentive to write a query that cannot fail.
Rejected: constants (`SELECT 1 AS evidence`), tautologies (a bare `COUNT(*)`),
proofs about columns the claim never mentions, and proofs returning zero.

Survivors land in `data/learned_schema.yaml`, bound to `DATASET_VERSION` and a
DB fingerprint, and are retrieved by `resolve_term` exactly like a curated
`Term`. `HARNESS_KNOWLEDGE` picks the source: `curated` (the default — the
original harness, unchanged), `discovered`, or `both`.

### Overfitting to the schema is the goal; overfitting to the questions is fraud

Learning that `plan_tier` is stale is what a new analyst does in week one.
Learning the answers to the eval is cheating, and it would be invisible in the
final number. So question-blindness is **structural**: `discover.py` has no
import, path or parameter through which a question can reach it, and
[`tests/test_discovery.py`](tests/test_discovery.py) asserts both that the
module never references `questions.yaml` and that the taxonomy names none of
this dataset's real columns.

### Three knowledge channels, not one

The comparison nearly measured nothing. The glossary is not the only place
curated knowledge lives:

| Channel | What it leaks |
|---|---|
| `glossary.py` | the 12 hand-written terms |
| `execute.py` `_DIRTY_COLUMNS` | every defect, by column, attached to **every query result** |
| `execute.py` zero-rows note | the three `status` vocabularies — D2's answer |
| `profile.py` `known_pairs` | the exact stale column — D3's answer |

The crib sheet in `execute.py` is a *bigger* channel than the glossary. All four
are now gated together by `memory.curated_hints_enabled()`, and the `discovered`
arm builds equivalent notes from its own claims — same trigger, same placement,
capped to the same length — so the arms differ in knowledge rather than in
plumbing. The generic probes (type drift, duplicates, value domains) stay on for
everyone; they are instrumentation, not answers.

### Measured

Qwen3.5-9B Q4_K_M, local, 31 questions, same database, same day. *clean*
excludes passes where the agent never actually finished — see below.

| Arm | Knowledge | Score | clean | Tokens |
|---|---|---|---|---|
| baseline | none | 20/31 — 64.5% | 20/31 | 51k |
| harness | curated (hand-written) | **27/31 — 87.1%** | 27/31 | 547k |
| harness | discovered, run 1 | 23/31 — 74.2% | 21/31 | 737k + 434k audit |
| harness | discovered, run 2 | 22/31 — 71.0% | 22/31 | 649k + 432k audit |

**The discovered artifact recovers roughly a quarter to a third of what the
hand-written glossary is worth** (+2 clean against baseline's 20, versus the
glossary's +7), for a one-time audit cost of ~430k tokens and ~5.5 minutes.

Per defect, the pattern is the one the analysis predicted: the model recovers
what is visible in the data and recovers nothing that is not.

| | baseline | curated | disc. 1 | disc. 2 |
|---|---|---|---|---|
| D5 type drift | 0/3 | 3/3 | 3/3 | 2/3 |
| D3 stale cache | 1/3 | 2/3 | 1/3 | 2/3 |
| D7 timezone | 1/3 | **3/3** | 1/3 | 1/3 |
| D10 units / tax | 1/4 | **3/4** | 1/4 | 1/4 |

D7 and D10 are exactly the two conventions no query can recover, and the
discovered arm never touches them. That is not a bug — it is the boundary, and
it is where the human interface below earns its place.

### What the audit actually found

Run 1 verified 4 claims (D2, D3, D5, D10-units); run 2 verified 6 (D1, D2, D3,
D6, D9). Neither found D4, D7 or D8. Nothing was rejected in either run: the
model did not try to game the contract.

### Three honest failures

**1. A wrong number propagated into an answer.** The stale-cache claim proved
itself with a query that omits `ended_on IS NULL`, so it reported 26 disagreeing
customers where the truth is 25 (the extra one is a mismatch on an *ended*
subscription). Q07 asks precisely that question, and the model answered 26.
A verified claim is **checked, not true** — and round 1's worst regression came
from a tool stating a wrong number confidently. Persisting one to a file is that
failure made permanent.

**2. A tautology in disguise passed.** The vocabulary-collision proof counts
invoices whose status is not a *subscriptions* value, plus subscriptions whose
status is not an *invoices* value: 1902 + 128 = 2030, i.e. two entire tables.
The finding is correct; the proof shows nothing. The whole-table flag caught it
and routed it to the top of the review queue, which is what that flag is for.

**3. The artifact is not stable.** Making `guidance` a required field — a
one-field schema change — produced an almost entirely different artifact. Only
D2 and D3 appear in both runs, and run 2 lost D5, which had been run 1's entire
source of lift. Any claim about "what the model discovers" needs several runs
behind it, not one.

Making the field required also got it *populated*, not *good*: three of six
claims filled `guidance` with a copy of their own verification query instead of
a usage pattern.

### The false-pass path, now observed live

The runtime review flagged that grading the last *successful* `execute_sql` lets
a run that errors or exhausts its steps still score, but could only demonstrate
it with a scripted model. Run 1 hit it for real: Q02 and Q15 both ran a correct
query, kept exploring to the 12-step limit, never reported an answer, and were
graded as passes. That is the 23/31 vs 21/31 gap in the table. The fix is an
explicit final-answer selection rather than a retroactive one; it is not done.

### Human review is an interface, deliberately

[`review.py`](harness/review.py) is a working API and CLI — `--queue`,
`--approve`, `--reject`, `--render` — and nothing more. The data here is
synthetic and its ten defects are known in advance, so a reviewer would only be
confirming what the generator already wrote down; building a review product
against a problem that does not exist would be the wrong thing to build.

What matters is that the seam sits where it will be needed, because on real
company data this is where the system stops being automatic. Two axes, kept
independent:

* a human can **veto** a machine-verified claim — the query ran and the
  conclusion was still wrong (failure 1 above is a live example);
* a human can **approve** a claim no query can prove. This is the slot for D7
  and D10: the machine can detect a shifted hour-of-day distribution, but only a
  person can say "that subgroup is US/Eastern".

Collapsing those into one score would discard exactly the cases that make a
human worth asking.

### Where this leaves the direction

Worth continuing, with the claim stated narrowly: **a generic defect taxonomy
plus mechanical verification recovers a minority — not a majority — of
hand-written schema knowledge, and recovers none of the knowledge that is not in
the data.** The honest next steps are stability across repeated audits, stronger
proof requirements (a contrast, not just a count), a stronger model for the
audit phase than for the answering phase, and fixing the false-pass path before
any of these numbers are quoted again.

---

## Status

**Verified — 175 tests passing, plus a 31/31 eval dry run:**
- 10-table schema, deterministic generator, all 10 defects asserted present in a live DB
- Read-only enforcement verified at the MySQL grant layer (`DROP` denied)
- Join inference, profiling, schema card, glossary retrieval
- 31 gold questions, every one executed; every `naive_sql` confirmed to produce a *different* answer
- Full agent loop — tool dispatch, message threading, error repair, step limits, grading —
  verified against a **scripted model**, so it runs with no API key, GPU or network
- Discovery: the verification contract's anti-gaming rejections, question-blindness,
  artifact round-trip, and that each of the four curated-knowledge channels is
  actually dark under `HARNESS_KNOWLEDGE=discovered`

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
