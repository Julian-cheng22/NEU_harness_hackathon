# Runtime verification — 2026-09-14

The harness produced genuine improvements on this database, including new questions, but the headline score and claims of stability are not reliable as written. This was a verification run, not an implementation change.

## Observed results

| Check | Baseline | Harness |
|---|---:|---:|
| Replay committed SQL with existing grader | 20/31 | 28/31 |
| New local inference on original questions | 20/31 (64.5%) | 27/31 (87.1%) |
| New local inference on eight frozen new questions | 3/8 (37.5%) | 6/8 (75.0%) |
| Original-question elapsed time | 33.64 s | 235.52 s |
| Original-question cumulative reported tokens | 51,358 | 546,956 |

Qwen3.5-9B Q4_K_M, local llama.cpp b10934-acecd5603, thinking disabled, both arms request temperature 0. The existing agent logic and grader were used unchanged. The server exposed four slots with its existing defaults. Model SHA-256 and serving properties are saved alongside this report. No Gemini or other paid inference was used.

The fresh questions were written to `fresh_questions_frozen.json` before inference. Their gold SQL was independently cross-checked using direct calculations on raw database rows. After inference, a separate scalar comparison with exact integer counts, half-cent monetary tolerance, and 0.0001 average tolerance also scored the fresh answers 3/8 versus 6/8. These are eight new questions on the same schema, conventions, and defect families, not a sealed or cross-database benchmark. Baseline still has less information and fewer inference steps, so these results do not isolate tool orchestration from supplied business knowledge or extra compute.

The harness's new original-question failures were Q05, Q07, Q10, Q14. Q07 previously passed, and now returns 28 instead of 25 by including NULL caches and ignoring the live-subscription filter. This rerun does not reproduce the claimed fixed 28/31 result or identical failure set. No current passing inference record has a non-null error.

Fresh failures: R02 still misses unit normalization when the currency flag is NULL. R05 computes the correct net lost-deal value but adds two extra columns despite the explicit request for just the total, so it fails the output requirement. The other six harness answers passed the independent scalar checks.

## Confirmed defects

1. **Q05 false negative.** Baseline returns won=114 and lost=64 in two columns. Those are the correct requested counts. Gold demands two rows even though the original question states no layout requirement. Correcting only this interpretation would increase the baseline count by one; it is not a complete corrected benchmark score because other gold issues remain.
2. **Q14 invalid entity-resolution oracle.** Raw DISTINCT and the gold normalization both return 121. All eight deliberately injected suffix variants remain separate from their originals. The gold query removes punctuation and case but cannot merge `Cedarbridge Holdings` with `Cedarbridge Holdings, Inc.`. Some base names also collide, so an independent entity identity mapping is needed to define the company count reliably.
3. **False passes after failure or wrong final output.** Controlled scripted responses through the unmodified agent demonstrate passes after a provider error, a step limit, and a final SQL answer of `SELECT 999` following an earlier correct count of 40. The agent retains the earlier successful SQL and the grader accepts its rows. This is an exploitable path, not evidence that the saved real-model successes used it.
4. **Comparator false positives.** The grader accepts 100,000,000 versus 100,000,001; 10,354,399.06 versus 10,354,399.46; inconsistent per-row swaps of two metrics; and reversed explicitly ordered results.
5. **Row-cap bypass.** Default SELECT returns 200 rows. An explicit LIMIT 250 returns 250, and a UNION ALL returns 256 despite the configured cap of 200. Setting `truncated=true` does not prevent fetching the extra rows.
6. **Join ambiguity disappears from initial context.** `employees.manager_id` is assigned to `product_catalog.catalog_id`, with the actual employee key listed as an alternative in inference output. The schema-card conversion retains only the wrong winner and loses that specific ambiguity and alternative.
7. **Live disconnect lifecycle bug.** Against the real HTTP app with a controlled scripted model, disconnecting left subsequent requests rejected as busy even after active model calls fell to zero. Explicit garbage collection released the abandoned stream and allowed a request again. Separately, explicitly closing the stream generator released the gate while its worker was still running and admitted a second worker. This confirms both lifecycle hazards, while distinguishing the actual HTTP observation from the direct generator test. The stream tests made no model API calls.

## Verification and preservation

- Existing normally collected suite: 149 passed, 7 skipped, 1 deselected. The deselected test attempts CREATE TABLE; SELECT-only permissions were checked with SHOW GRANTS instead.
- Explicit generator self-check suite: 14 passed. Total executed passing tests: 163.
- Generator `--check`: committed seed reproduced byte-for-byte.
- All ten live tables matched the generated rows after accounting for SQL storage types.
- All 31 gold queries executed and returned nonempty results. This verifies execution, not independent semantic correctness.
- Saved SQL replay reproduced every saved pass/fail flag.
- All 41 pre-existing workspace files, including code, configuration, seed, and committed results, retained their original SHA-256 hashes. Database table fingerprints remained unchanged during verification.
- Local model and project database container started for this review were stopped afterward. No existing code or mechanism was edited. All new review scripts, results, and logs are confined to this directory.

## Evidence files

- `verification_summary.json`: final scores, stricter fresh-answer checks, integrity results.
- `audit.json`: saved-query replay, Q05/Q14 evidence, comparator and row-cap reproductions, scripted failure cases, independent fresh-question oracles.
- `original-*-local.json`, `fresh-*-local.json`: new inference outputs with SQL and usage.
- `fresh_questions_frozen.json`: the eight questions frozen before inference.
- `stream_check.json`: actual HTTP disconnect and direct generator-close observations.
- `pytest_suite.log`, `pytest.log`, `determinism.log`, `live_vs_generator.json`: test and data checks.
- `model_sha256.json`, `server_properties.json`, `original_hashes.json`, `db_fingerprint.json`: reproducibility and integrity evidence.

Next implementation priorities are independent gold answers, a semantic output contract, explicit final-answer selection and completion checks, strict limits, preserved join uncertainty, and reliable stream cleanup. A larger frozen evaluation with knowledge and compute ablations is still needed before claiming generalizable harness performance.
