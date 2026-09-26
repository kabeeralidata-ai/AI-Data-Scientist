# Overhaul Progress

_Task: fix 6 root causes (name-guessed column roles, blind generic cleaning, forced
targets, fixed-template reports, duplicated code paths, no automatic verification) via a
benchmarked, phased overhaul. This file is the single source of truth for where the work
stands — a new session with no memory of this conversation should be able to read this
file alone and continue correctly._

**Baseline commit:** `5a986d2` "Baseline before overhaul" (2026-09-25)

## Status at a glance

| Phase | Status | Notes |
|---|---|---|
| 0 — Audit | ✅ Done | See below |
| 1 — Benchmark harness | ✅ Done (harness itself) | Built and runs correctly: 26/27 checks pass on the 2 datasets that exist; 4/6 datasets still missing (see "BLOCKER") so full 6-dataset coverage is NOT yet complete |
| 2 — Data Understanding | ⬜ Not started | `data_understanding_service.py` already exists from a prior task but is NOT yet the single source of truth for target selection — see Phase 0 finding #1 |
| 3 — Cleaning | ⬜ Not started | Partially done in a prior task (`column_roles`-aware, backward compatible) — needs the plausibility-range and grouped-imputation work this phase specifies |
| 4 — Planner | ⬜ Not started | `transaction_analysis_service.build_analysis_plan()` exists for transaction logs only; no segmentation/prediction planner yet |
| 5 — Modeling safety | ⬜ Not started | `ml_service.py` has real leakage detection; `return_prediction_service.py` does NOT use it (duplicated, unsafe path) — see Phase 0 finding #2 |
| 6 — Adaptive report | ⬜ Not started | Report sections are hardcoded template branches (model/clusters/transaction_analysis), not truly plan-driven — see Phase 0 finding #3 |
| 7 — AI reliability | ⬜ Not started | Gemini service is unified and cached for supervised/clustering paths; transaction-log AI narrative does NOT use the same DB cache — see Phase 0 finding #4 |
| 8 — App consistency | ⬜ Not started | No pipeline versioning exists yet |
| 9 — Final verification | ⬜ Not started | Depends on all above |

## BLOCKER — 4 of 6 benchmark datasets do not exist yet

Only `coffee_shop_transactions.csv` and `karachi_food_delivery_dataset.csv` exist in
`backend/tests/data/`. Missing, with specific numeric acceptance criteria that must come
from real, provided data (fabricating data to hit them would violate this task's own
"never tune to the answer keys" rule and this project's standing no-fake-data rule):

- `customer_churn_dataset.csv` (+ implicit ~35% churn rate, specific top-driver columns)
- `retail_sales_dataset.csv` (+ Electronics-top-category, Nov-Dec peak expectations)
- `customer_retention_training.csv` + `new_customers_to_predict.csv` (500 rows, true
  returner count 223)
- `telecom_subscribers_usage.csv` + `answer_key_true_segments.csv` (true segment labels
  for a ≥85% match-rate check)

**Action needed from the user**: provide these files in `backend/tests/data/`. Phase 1's
harness (below) is built to check all 6 datasets' full check lists but will report
`SKIPPED (file not found)` for these 4 until then — Phase 1 is not "complete" (per this
task's own "never start the next phase while benchmark checks for completed phases are
failing" rule) until they're available and passing.

## Phase 0 — Audit findings

### Path map (upload → report), current state

```
Upload (api/datasets.py)
  → dataset_service.save_upload() → build_dataset_profile()  [stores profile_json]

Quality/Clean (api/datasets.py)
  → data_understanding_service.understand_dataset()  [column_roles, dataset_type, formula_columns]
  → cleaning_service.analyze_quality() / clean_dataset(column_roles=...)

Auto Analyze (services/auto_analyze_service.py) — ONE orchestrator, branches 3 ways:
  1. Supervised (target found):
     dataset_service.score_target_candidates()  ← OLD, name-hint-first target picker
       → ml_service.train_models()  [real leakage detection, baseline, weak-model flag]
       → report_service.generate_report(model=...)
  2. General/clustering (no target found):
     ml_service.run_clustering_analysis()
       → report_service.generate_report(clusters=...)
  3. Transaction log (dataset_type == "transaction_log"):
     transaction_analysis_service.run_full_transaction_analysis()
       → customer_analytics_service (RFM)
       → return_prediction_service.run_return_prediction()  ← OWN bespoke training code
       → forecast_service.forecast_weekly_revenue()
       → report_service.generate_report(transaction_analysis=...)

Manual Modeling tab (api/models.py POST /api/models/train)
  → ml_service.train_models()  [SAME function as Auto Analyze path 1 — good]
  → target picked by the USER from a dropdown pre-filled by
    dataset.suggested_target_column, which ALSO comes from
    dataset_service.score_target_candidates() (via suggest_target_column) — same old
    name-hint-first picker, not data_understanding_service's role classification.

Manual Prediction tab (api/predictions.py)
  → prediction_service.predict() / predict_batch()  [only works for ml_service-trained
    MLModel rows — return_prediction_service's model is NOT reachable here at all]

Report generation (api/reports.py)
  → report_service.generate_report() → report_context_service.build_report_context()
    → report.html (Jinja) — ONE template, but section visibility is a hardcoded
      if/elif chain (model / clusters / transaction_analysis / no_model_reason), not a
      composable, plan-driven section list
```

### Finding 1 — target/column-role duplication (root causes #1, #3)

`dataset_service.score_target_candidates()` (pure name-hint tiers +
cardinality/description bonus — no numeric-relationship testing, no formula-column
awareness) is STILL the function that actually picks/suggests the supervised target,
in BOTH Auto Analyze and the Modeling tab's pre-filled dropdown.

`data_understanding_service.classify_columns()` / `detect_formula_columns()` (built in a
prior task, data-evidence-first, formula-aware) exists and is used for dataset-TYPE
detection and cleaning's `column_roles`, but is **never consulted when picking a
target** — so a formula column or an obviously-identifier-shaped column could still be
suggested as a target by the old function in a dataset `score_target_candidates` hasn't
been taught about. This is the literal root cause of "a prediction target always forced"
(root cause #3) too: `score_target_candidates` has no "no plausible target" signal beyond
an empty list, and even then, the OLD auto_analyze code (before this session's earlier
fixes) used to hard-fail; the current code falls back to clustering/general analysis, but
that fallback logic lives in `auto_analyze_service.py`, not in a shared planner Phase 4
can build on.

**Phase 2/4 must**: merge target-candidate scoring INTO `data_understanding_service`
(using its role/formula evidence as the primary signal, name hints as a tie-breaker per
this task's explicit instruction), and make the Modeling tab's dropdown and Auto
Analyze's target detection call the SAME merged function.

### Finding 2 — modeling safety duplication (root cause #5)

`ml_service.py` has real, tested leakage detection (`detect_leakage`,
`detect_pairwise_leakage`, `detect_derived_metric_features`, `detect_high_correlation_features`),
baseline comparison, and weak-model flagging (`evaluate_model_strength`).

`return_prediction_service.py` (built in a prior task for the coffee-shop transaction-log
path) does **none of this** — its `NUMERIC_FEATURES`/`CATEGORICAL_FEATURES` are a fixed,
hardcoded list (recency/frequency/monetary/refund_total/loyalty + favorite branch/
category), never leakage-checked, no baseline comparison, no weak-model warning threshold
shared with ml_service's. It also doesn't save a reusable pipeline via `prediction_service`
— its forward predictions are computed inline and never become an `MLModel` row, so
there's no batch-prediction UI path for it (unlike the customer_retention_training.csv
benchmark's explicit requirement: "Batch prediction on new_customers_to_predict.csv using
the saved pipeline").

**Phase 5 must**: extract ONE shared feature-selection/leakage/baseline/weak-model module
that `ml_service.train_models()`, `return_prediction_service.py`, and any Phase 4
segmentation/forecasting training all call — and route return-prediction's trained
pipeline through the SAME `MLModel`/`prediction_service` machinery so batch prediction
"just works" for it like any other trained model.

### Finding 3 — report is a hardcoded template, not plan-driven (root cause #4)

`report.html`'s section visibility is `{% if model %}...{% elif clusters %}...{% elif
transaction_analysis %}...{% elif no_model_reason %}...{% endif %}` — a fixed enumeration
of the branches that happened to exist when each was built, including a section-numbering
hack (section "5." is reused by 3 mutually-exclusive branches, since true renumbering
would require rewriting every subsequent section). Adding a genuinely new analysis type
(e.g. Phase 4's segmentation) means editing this chain again, not composing from a plan.

**Phase 6 must**: have the PLAN (from Phase 4) declare which named sections apply and in
what order, and have the template iterate a section list rather than branch on which
top-level context key happens to be truthy.

### Finding 4 — AI caching inconsistency (root cause #5/#6, Phase 7)

`gemini_service.py`/`ai_service.py` are genuinely unified (one provider service, real
error classification, in-process TTL cache for passive status polling). The DB-persisted
`AIInsightCache` table (keyed by dataset `cleaning_version` + model id, survives restarts/
reloads) is used for the supervised and clustering/general Auto Analyze paths, but the
transaction-log path's AI narrative is a single fresh call every run with NO persistent
cache entry — "Regenerate" vs. "reload" isn't distinguished for it at all yet.

**Phase 7 must**: extend `AIInsightCache`'s key scheme (or the pipeline-version field
Phase 8 introduces) to cover the transaction-log narrative too.

### Finding 5 — no automated benchmark harness (root cause #6)

Confirmed: this session's ~241 backend tests are unit/integration tests (mocked AI,
synthetic/known-shape CSVs, or the ONE real coffee-shop dataset with its own bespoke test
file). There has never been a single harness that runs the full real pipeline against
multiple real datasets and checks domain-specific expected outcomes in one pass/fail
table. Phase 1 (this session) builds exactly that.

## Phase 1 — Benchmark harness

Built at `backend/tests/benchmark/`:
  - `harness.py` — core: isolated per-dataset SQLite DB, real HTTP calls through the real
    FastAPI app (upload -> auto-analyze -> confirm -> report), `CheckResult`/
    `DatasetBenchmark` dataclasses, `print_report()` (pass/fail table + summary line).
  - `checks_<dataset>.py` x6 — one module per benchmark dataset, each a `run(client) ->
    list[CheckResult]` implementing that dataset's exact check list from the task spec.
  - `run_benchmark.py` — CLI entry point: `./venv/Scripts/python.exe -m
    tests.benchmark.run_benchmark` from `backend/`. Exit code 0 iff every present
    dataset's checks all pass AND no dataset is missing (an incomplete benchmark is never
    reported as passing).

Two real bugs were found and fixed IN THE HARNESS ITSELF while getting it running
(neither was an app bug):
  1. Windows console (cp1252) crashed on em-dash characters in real app messages —
     fixed by reconfiguring stdout/stderr to UTF-8 in `run_benchmark.py`.
  2. Three check modules used `app.core.database.SessionLocal` (the PRODUCTION session
     factory) instead of the harness's isolated per-run database, causing a "no such
     table" error — fixed by exposing `client.db_session_local` from `make_client()`.

### Real, current benchmark output (2026-09-26, after both harness fixes)

```
karachi_food_delivery_dataset.csv: 11/12 PASS
  FAIL: 'promised_time_min' KEPT as a training feature
    — it was NOT kept; excluded along with delivery_time_min.
coffee_shop_transactions.csv: 15/15 PASS
customer_churn_dataset.csv / retail_sales_dataset.csv / customer_retention_training.csv /
telecom_subscribers_usage.csv: SKIPPED — file not found

26/27 checks passed — 2 dataset(s) run, 4 skipped (missing file), 0 errored
```

### Finding 6 — confirmed root cause of the ONE real failure (Phase 5 target)

`ml_service.py::detect_pairwise_leakage()` (lines ~336-353): when two numeric columns
`(a, b)` together reproduce a binary target almost perfectly (e.g. `late_delivery =
delivery_time_min > promised_time_min`), the function excludes **both** columns
symmetrically:
```python
for col, other in ((a, b), (b, a)):
    if col in flagged: continue
    flagged.add(col)   # <- excludes BOTH sides of the pair, unconditionally
```
The task (and this benchmark) explicitly wants only the OUTCOME-side column
(`delivery_time_min`, only known after the delivery happens) excluded —
`promised_time_min` is legitimate information known BEFORE the outcome and must be kept.
The function has no concept of "which side of the pair is history vs. post-outcome" —
that's exactly the "history vs. post-outcome" role Phase 2 must add, and Phase 5 must
make the pairwise check consult it (only drop the post-outcome side of a leaking pair,
not both) rather than dropping symmetrically.

## Next step

Phase 1 harness itself is done and working. Two paths forward, not mutually exclusive:
  (a) Waiting on the 4 missing dataset files from the user for full 6-dataset coverage.
  (b) Proceeding to Phase 2 (Data Understanding merge — finding #1) now, since it's
      independently useful and fully testable against the 2 datasets already present,
      and Finding 6 above gives Phase 2/5 a concrete, precise bug to fix as part of that
      work.
Proceeding with (b) while flagging (a) to the user.
