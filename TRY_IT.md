# Try the dashboard

For teammates who want to open the result and click around. You do **not** need
Docker, a database, a model, an API key or a network connection.

The page shows the measured result: baseline vs harness on the same 31 questions
against the same database, broken down by which kind of data defect each question
hides. Every number is one click from the SQL that produced it.

Three ways in. Stop at the first one that answers your question.

---

## Path A — open the link (no setup at all)

**<https://usp787.github.io/NEU_harness_hackathon/>**

Published from `web/static/` on every push to `main`. It is the same page as the
other two paths, so unless you are changing code, you are done here.

---

## Path B — run it from a clone (about a minute)

```bash
git clone https://github.com/usp787/NEU_harness_hackathon.git
cd NEU_harness_hackathon
```

Then **double-click `web/static/index.html`**. That is the whole procedure.

The results are committed to the repo as `web/static/data.js`, so a fresh clone
already has real numbers in it — there is no build step and nothing to install.

If your browser is fussy about `file://` URLs, serve the folder instead (any
Python 3 will do, no venv):

```bash
cd web/static
python3 -m http.server 8000     # -> http://127.0.0.1:8000
```

On Windows use `py -3 -m http.server 8000`. Bare `python`/`python3` there is
often the Microsoft Store stub, which exits instantly and leaves you with a dead
port and no error.

Both give you the identical page. The second is only nicer because reloads and
the browser devtools behave normally.

---

## Path C — run the real server (about five minutes)

Do this if you want to exercise `web/server.py` itself rather than the baked
snapshot: the `/api/results` and `/api/health` endpoints, and the banner logic
that works out which dependency is missing.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/python -m web.server                 # -> http://127.0.0.1:8000
```

On Windows swap `.venv/bin/python` for `.venv\Scripts\python` throughout. If
port 8000 is taken, `--port 8011`.

You do not need `python -m web.build_data` — that only refreshes the offline
copy after someone re-runs the eval, and the committed copy is current.

---

## The live panel will be greyed out. That is correct.

Scroll to **"Ask it live"** and you will find the *Run both arms* button
disabled, with a grey status pill and a notice explaining what is missing. This
is designed behaviour, not a bug, and it is the single most likely thing to get
reported as one.

Asking a genuinely new question needs a seeded MySQL container *and* a local
`llama-server` holding a 9B model on a GPU. That combination currently exists on
one machine. Everything above the live panel — the headline, the per-defect
chart, all 31 questions with their SQL and the harness tool traces — is read
from committed results and is complete without it.

| Pill says | Means |
|---|---|
| `no server` | Path A or B. Expected — there is no backend to reach. |
| `database unreachable` | Path C without `docker compose up -d`. Expected. |
| `model unreachable` | Path C with a database but no model. Expected. |
| `qwen3.5-9b · db ok` | You somehow have the full stack. Go ahead and ask it something. |

Deep links like `#ask=Q11` exist but only do anything on a machine with the full
stack, so ignore them.

---

## What is worth poking

- **Click a bar in "Accuracy by defect."** It filters the question list beneath
  it. `D5`, `D10` and `D3` are where the gap is widest. The **Chart / Table**
  toggle shows the same data as numbers if you prefer them.
- **Read the control row.** It is the honesty check: questions with no defect at
  all, where the harness should not help. It is on the front page rather than
  buried, deliberately.
- **Open `Q11`, `Q13` or `Q23`.** Each row expands to the gold SQL, what each arm
  actually wrote, and the harness's tool trace. `Q11` is the clearest single
  illustration of the thesis — `MAX()` over a VARCHAR compares lexically, so
  `'N/A'` beats `'400'` and nothing errors.
- **Find `Q14`.** The harness gets it wrong and the baseline gets it right. It is
  in there on purpose; if you think the framing of it is too soft, say so.
- **Toggle dark mode** (top right) and **narrow the window** — both are meant to
  hold up, and a projector is a strange aspect ratio.

The **Run** picker at the top right listing a single option is expected: it shows
one entry per model provider, and every committed run is `local`. The extra files
in `eval/out/` are an ablation and are intentionally not reachable from it.

---

## Reporting something

Worth a message: numbers that disagree between the offline page and the served
one, a question row that will not expand, a layout that breaks, a claim in the
prose you think the data does not support.

Not worth a message: the disabled live panel, the single-entry run picker, a
greyed status pill. Those are all above.

Deeper context lives in [`README.md`](README.md) — the thesis, the dataset and
how the defects were built. [`DEMO.md`](DEMO.md) is the presenter's runbook and
assumes the full stack; you do not need it to review the page.
