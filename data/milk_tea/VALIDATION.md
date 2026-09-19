# Verification record — 2026-09-19

Tested with Python 3.12.14 and a disposable local **MySQL 8.0.35** server.
The original SaaS and new milk-tea fixtures were loaded into different schemas.
No paid inference or actual model generation was used.

| Check | Observed result |
|---|---|
| New fixture tests, including real database integration | **70 passed**, no skips |
| Existing SaaS suite | **175 passed, 7 skipped** |
| New gold dry evaluation | **20/20** |
| Existing gold dry evaluation | **31/31** |
| New gold vs independent Python Decimal oracle | **20/20 exact numeric matches** |
| Hand-authored naive SQL | **17/17 execute successfully and differ from gold** |
| New generator drift check | Passed, including `PYTHONHASHSEED=7` |
| Existing generator drift check | Passed; original seed unchanged |
| Package dependency consistency | `pip check`: no broken requirements |
| Web `/api/results` with milk-tea selected | 20 MTQ questions, 8 MT defects, milk-tea runs only |
| Frontend JavaScript syntax | `node --check` passed |
| Git whitespace check | `git diff --check` passed |

The tests cover document header totals, return-to-sale references and amounts,
prior delivery of approved returns, production/return stock-in mirrors,
identical replay payloads, nonnegative warehouse/material/batch balances,
read-only execution, real Cartesian-product rejection, and refusal to overwrite
an already populated database. A scripted fake model also exercises the actual
schema/join builder, glossary, profiler and SQL tool through the agent loop.
That test checks integration, not model intelligence.

Selected measured counterexamples:

| Question | Gold result | Naive result |
|---|---:|---:|
| MTQ01: approved sales quantity, cups | 121,217 | 22,195 |
| MTQ05: approved refund, CNY net | 21,957.00 | 20,097.00 |
| MTQ07: purchase inflow, cups | 5,620,000 | 6,020,000 |
| MTQ09: approved order value, CNY gross | 994,473.98 | 1,988,947.96 |
| MTQ11: completed production inflow, cups | 573,200 | 1,146,400 |
| MTQ13: maximum valid weight, kg | 499.7500 | `N/A` |
| MTQ14: average valid weight, kg | 250.6060 | 235.7975 |
| MTQ20: on-hand stock, cups | 6,125,768 | 6,866,483 |

Gold, naive and expect live in `questions.yaml`; assertions are in
`tests/test_milk_tea.py`. Reproduction commands and environment variables are
in the adjacent README.

**Not measured:** real small-model baseline/harness accuracy, token cost,
latency, or automatically discovered knowledge performance on this dataset.
The dataset is a controlled synthetic semantic benchmark, not a calibrated
simulation of real milk-tea demand, inventory turnover or manufacturing costs.
