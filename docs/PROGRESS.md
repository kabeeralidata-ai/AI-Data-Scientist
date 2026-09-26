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
| 1 — Benchmark harness | ✅ Done | All 6 datasets present and running. |
| 2 — Data Understanding | ✅ Done (this round's scope) | Role-based post-outcome detection now lives in `data_understanding_service` (Findings 13-15) — genuinely target-independent, structural evidence, replacing the old correlation-based heuristic. `detect_dataset_type` reordering fix. Full benchmark: **61/64** (2026-09-26). Broader Phase 2 scope (new roles: count/rate/free-text, per-column unit tracking, Gemini tie-breaker for targets, full target-scoring merge) still open — see "Next step" — deliberately deferred, not required for this round's stated goal (role-based post-outcome + complaints_last_6_months). |
| 3 — Cleaning | ✅ Done (this round's scope) | Plausibility-range cleaning added (age biological ceiling + time-unit-within-period physical ceiling — Finding 16). Telecom's 4 fraud call-minute values and 3 impossible ages (134/150/212) now genuinely corrected, not just statistically flagged. |
| 3.5 — Segmentation methodology | ✅ Done | `run_clustering_analysis()` rebuilt to standard methodology: behavioral/usage features only (demographics/age describe segments afterward, never form them), skewed columns log-transformed, k chosen from silhouette + GMM BIC + bootstrap stability together — never silhouette alone, never the answer key. This resolved Finding 17 for real: k=4 exactly, 99.9% match rate as a post-hoc diagnostic. See Finding 18. |
| 4 — Planner | ✅ Done | New `planning_service.build_analysis_plan()` is the single shared entry point EVERY Auto Analyze run calls — previously only transaction-log datasets got an explicit plan object at all; now every dataset type does (`job.result_json["plan"]`), delegating to `transaction_analysis_service`'s existing viability math for transaction logs rather than duplicating it. See Finding 19. Execution engines (train/cluster/transaction-analytics) and the existing pause/resume UX are unchanged — a deliberate, lower-risk scope than a full control-flow rewrite. **Follow-up (Finding 20):** every plan entry now carries a `reason` and a `confidence` level (high/medium/low/none), plus an overall `plan["confidence"]`; segmentation — previously the one path with NO confirmation step at all — now always pauses for confirmation like the other two paths; `confirm-plan` accepts an optional `target_column` to override the plan entirely. Surfaced and fixed a real pre-existing bug along the way: coffee shop's plan reported "27028 days of history" due to a planted invalid date inflating the span calculation. |
| 5 — Modeling safety | ✅ Done (this round's scope) | `return_prediction_service.py` now runs the SAME leakage detection, baseline comparison, and weak-model flagging `ml_service.train_models()` uses (extracted `compute_baseline_comparison()` as a shared function) — verified against real coffee-shop data: `leakage_warnings=[]`, model F1 0.929 vs. baseline 0.538, `is_weak=False`. See Finding 21. Deliberately scoped narrower than a full rewrite: return-prediction still uses its own training orchestration (a genuinely different data shape — a time-cutoff split over customer-level aggregates, not `train_models()`'s raw-dataframe-in assumption), and does NOT yet save an `MLModel` row, so batch prediction still isn't available for it — the remaining piece of Phase 0 Finding 2, documented as still open. |
| 6 — Adaptive report | ✅ Done | Business question/KPIs/currency/date-ranges/refund-vs-loss/recommendations/segmentation/RFM-naming/formatting all fixed and verified against real data for all 6 benchmark datasets, both via the benchmark harness and by generating real reports in the live running app. Full benchmark **67/67** (including the long-failing Nov-Dec peak check). See Finding 22. |
| 7 — AI reliability | ✅ Done | The AI-narrative Retry mechanism and the DB-persisted AIInsightCache now cover all three analysis types (model/clustering/transaction-log), not just the supervised path — see Finding 23. |
| 8 — App consistency | ✅ Done (this round's scope) | Return-prediction now has a Predictions-tab UI (per-customer probability/risk group, CSV/Excel download) — Finding 21's remaining gap. Stale-server prevention: `dev.py` (always `--reload`), `/api/health` reports the running git commit, Settings shows it, and the benchmark/a new `scripts/check_live_server.py` check it against local HEAD. See Finding 24. |
| 9 — Final verification | ⬜ Not started | Depends on all above |

## All 6 benchmark datasets now present (BLOCKER resolved 2026-09-26)

The user supplied all 4 previously-missing files in `backend/tests/data/` on 2026-09-26
(`customer_churn_dataset.csv` needed a rename from `customer_churn_dataset (1).csv`).
All 10 benchmark files (6 datasets + 4 answer keys / batch-prediction inputs) are present.

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

### Finding 6 — confirmed root cause of the ONE real failure — ✅ FIXED

`ml_service.py::detect_pairwise_leakage()` (lines ~336-353): when two numeric columns
`(a, b)` together reproduce a binary target almost perfectly (e.g. `late_delivery =
delivery_time_min > promised_time_min`), the function excluded **both** columns
symmetrically:
```python
for col, other in ((a, b), (b, a)):
    if col in flagged: continue
    flagged.add(col)   # <- excludes BOTH sides of the pair, unconditionally
```
The task (and this benchmark) explicitly wants only the OUTCOME-side column
(`delivery_time_min`, only known after the delivery happens) excluded —
`promised_time_min` is legitimate information known BEFORE the outcome and must be kept.

**Fix applied** (`ml_service.py`, new `_outcome_side_of_pair()`): a real, data-evidence
signal (never a column name) — verified against the actual Karachi data before writing
any code: `promised_time_min` has only 2 distinct values (55, 70 — a fixed SLA tier),
while `delivery_time_min` has 127 (a continuous realized outcome). When one side of a
leaking pair has >=3x fewer distinct values than the other, only the high-cardinality
(outcome) side is excluded; when cardinality is similar (ambiguous — verified against the
existing synthetic test `_derived_flag_csv`, ratio ~1.18x), both sides are still excluded,
identical to the old behavior. This means the fix is additive: it only changes behavior
in the case it was built to fix, never in the ambiguous case the original tests covered.

Verified:
  - New regression test `test_pairwise_leakage_excludes_only_the_outcome_side_when_cardinality_is_asymmetric`
    (SLA-tiered synthetic data, mirroring the real Karachi shape) — passes.
  - Existing `test_training_excludes_pairwise_derived_leakage_not_caught_by_single_column_correlation`
    (ambiguous-cardinality synthetic data) — still passes unchanged, confirming no
    regression in the case the fix doesn't apply to.
  - 2 pre-existing Karachi end-to-end tests asserted the OLD (buggy) exclusion of
    `promised_time_min` — updated to assert it's correctly KEPT (`test_karachi_end_to_end.py`).
  - Benchmark: karachi_food_delivery_dataset.csv now **12/12 PASS** (was 11/12).
  - Full backend suite: 241/241 passing (240 pre-existing + 1 new regression test; the 2
    updated Karachi tests are part of the pre-existing count, now passing on the
    corrected assertion instead of the old one).
  - Full benchmark: **27/27 checks passed** across both datasets present.

### Finding 7 — formula columns could still be suggested as targets — ✅ FIXED

`dataset_service.score_target_candidates()` never consulted `detect_formula_columns()`'s
output at all — a formula column (total = quantity × price − discount) with a
target-hint-matching name (e.g. "total_amount" matching the "amount" hint) could still
score well and be suggested as a target, directly violating Phase 4's "never choose...
formula components... as targets" rule.

**Fix applied**: `score_target_candidates()` gained an optional `formula_columns` param
that excludes any detected formula column from candidacy entirely, before any name-hint
scoring runs. `auto_analyze_service.py`'s call site now passes the `formula_columns`
already computed by the Data Understanding step. `dataset_service.suggest_target_column()`
(the lightweight Modeling-tab dropdown pre-fill, called right after upload before Data
Understanding has run) does NOT yet pass this — a known, documented remaining gap, not
silently claimed fixed.

Verified: new unit test constructs a column that WOULD score well by name+cardinality
alone, confirms it's suggested without formula info, then confirms it's excluded when
formula_columns is supplied. Full suite: 243/243 passing. Full benchmark: 27/27 (no
regression from either fix).

## Full 6-dataset baseline — Phase 1 (2026-09-26, after Findings 8-12) — 60/64 — superseded below

```
customer_churn_dataset.csv:        10/10 PASS
retail_sales_dataset.csv:           8/10 PASS  (2 documented gaps, see below)
karachi_food_delivery_dataset.csv: 12/12 PASS
customer_retention_training.csv:    8/8  PASS
coffee_shop_transactions.csv:      15/15 PASS
telecom_subscribers_usage.csv:      7/9  PASS  (2 documented gaps, see below)

60/64 checks passed — 6 dataset(s) run, 0 skipped, 0 errored
```

Full backend test suite: **243/243 passing** (after fixing one test fixture collision —
see Finding 9).

The 4 remaining failures, each individually diagnosed (none is a silent/unknown gap):

1. **retail: `customer_rating` not excluded as post-outcome** — root-caused in Finding 10
   below; a real, disclosed limitation of correlation-based post-outcome detection, not a
   quick-fixable bug. User decision (2026-09-26): leave as-is at the time, revisit as part
   of the proper Phase 2 role-based merge — **done, see Phase 2 baseline below.**
2. **retail: Nov-Dec revenue peak not visible in report** — pre-existing, documented Phase
   4/6 gap (report architecture has no seasonal-peak narrative for the plain-regression
   path yet; only the transaction-log path's forecast section has this).
3. **telecom: chosen k = 5, not 4** — root-caused in Finding 12 below (4 physically-
   impossible call-minute values distort the cluster structure); the general fix belongs
   in Phase 3 (plausibility-range cleaning), not implemented yet.
4. **telecom: match rate vs. answer key** — pre-existing, documented Phase 4/5 gap:
   `run_clustering_analysis()` doesn't expose per-row cluster assignments via the API at
   all yet, so this check cannot run (not merely fail).

## Finding 8 — `score_target_candidates` forced a target onto EVERY dataset (root cause #3, confirmed) — ✅ FIXED

With all 6 datasets available, `telecom_subscribers_usage.csv` (spec: "NO target;
segmentation plan") instead paused Auto Analyze at `awaiting_target_confirmation` —
proof that a target was being force-suggested where none should exist, the literal
def of root cause #3.

Root cause, verified directly: `score_target_candidates()`'s cardinality/shape tier
(`"numeric with many distinct values (a regression candidate)"`, `"binary column"`,
`"low-cardinality column"`) scored a column positively with **zero name or description
evidence** — true of almost every measurement column in almost every dataset (verified:
`video_streaming_hours_month`, `social_media_share_pct`, `night_data_share_pct`, `gender`,
`plan_type` all scored positively in telecom with no outcome-semantic signal at all).

**Fix applied**: a column can only become a target *candidate* if something ties it to a
business outcome — a `TARGET_HINT_TIER1/2/3` name match, or a project-description mention.
Cardinality/shape now only adjusts the score of an already-qualified candidate; it can
never qualify one by itself. `TARGET_HINT_TIER3` was also expanded with `late`/`delayed`/
`overdue` (verified necessary: without it, `late_delivery` — karachi's real, correct
target — stopped qualifying too, since "late"/"delivery" matched no existing hint; this
was caught by a real test regression, not by inspection).

Verified against all 6 real datasets before/after:
  - telecom: candidates go from 3 false positives → **0** (correctly proceeds to
    clustering).
  - karachi: `late_delivery` still qualifies (score 5.0, sole candidate).
  - churn/retail/retention: `churn`/`sales_amount`/`will_return_next_90_days` all still
    qualify and remain in the top-3 suggested candidates.

## Finding 9 — single-column deterministic leaks invisible to Pearson correlation — ✅ FIXED

`customer_retention_training.csv`'s `orders_next_90_days` (a future-window order count)
is a **perfect** predictor of the target `will_return_next_90_days` — verified directly:
`orders_next_90_days == 0` maps to `No` with 100% agreement, `> 0` maps to `Yes` with 100%
agreement (1685 + 1340 of 3025 rows, zero exceptions) — but its **Pearson correlation**
with the target is only 0.84, below `detect_leakage`'s 0.95 threshold, because the
feature's extra graded variation among the `Yes` rows (1-5 orders) dilutes the linear
correlation even though the boolean split is exact.

**Fix applied**: `detect_leakage()` now also computes, for binary targets, the best
single-threshold-split agreement (the same "does `a > threshold` agree with the target"
idea `detect_pairwise_leakage` already uses between two columns, applied here to one
column against a scanned threshold) — flagged at `>= 0.97` agreement. Verified this
doesn't false-positive on the legitimate top features of churn/retention/karachi (next-
closest scores were 0.65-0.68 in churn/retention, 0.89 in karachi for a column already
excluded by the existing pairwise check anyway) before enabling it.

**Regression caught and fixed**: this correctly flagged `tests/test_smart_features.py`'s
`test_continuous_numeric_columns_are_not_flagged_as_id_like` fixture, whose synthetic
`churn = 1 if charge > 50 else 0` is an exact deterministic function of the very feature
the test meant to verify stays included — a real single-column leak by the test's own
construction, unrelated to what the test was actually checking (is_id_like exclusion).
Fixed by decoupling the fixture's target from the feature under test.

## Finding 10 — post-outcome detection's "evidence" can be near-meaningless noise — DOCUMENTED, not fixed (user decision)

Added `"return"` to `POST_OUTCOME_NAME_HINTS` so `returned` (retail) is name-matched
alongside `customer_rating`. This fixed `returned`'s exclusion, but **not**
`customer_rating`'s — investigated directly rather than re-tuning blindly:

```
customer_rating vs sales_amount:  correlation = 0.22%
returned        vs sales_amount:  correlation = 3.6%
complaints_last_6_months vs a synthetic telecom target: correlation = 0.85%-14%
```

`detect_post_outcome_features()`'s bar is *relative* (correlation `>=` the median of all
other features' correlations, a "noise floor"), not absolute. All of the above are
statistically indistinguishable near-zero noise — there is **no threshold value** that
keeps `customer_rating` (0.22%) flagged while rejecting a hypothetical `telecom`
false-positive at 0.85%, since 0.22% < 0.85%. Proven experimentally: real, hypothetical-
target tests against telecom showed `complaints_last_6_months` clearing the noise floor
and landing in "review" (defaulted-excluded) on correlations as low as 0.85%-1%.

**Why this isn't fixed now**: no correlation-based threshold can resolve this — the
actual signal for "returned"/"customer_rating" being post-outcome is **temporal**
(a return or rating happens after a sale), which the current mechanism has no way to
observe. The architecturally correct fix is Phase 2's own stated goal: post-outcome-ness
should be a column **role** in `data_understanding_service`, independent of whichever
target is chosen, not a correlation-vs-arbitrary-target heuristic in `ml_service`.

**User decision (2026-09-26)**: leave as-is for now. Telecom's real Auto Analyze pipeline
is already safe today — not because of this mechanism, but because Finding 8 makes
telecom return zero target candidates, so `detect_post_outcome_features` never runs for
it at all. `customer_rating` staying in retail's feature set is a known, documented,
low-severity gap, not a regression.

## Finding 11 — batch prediction rejected raw uploaded columns (Phase 5 requirement) — ✅ FIXED

`customer_retention_training.csv`'s batch-prediction spec ("raw columns, dates processed
by the saved pipeline") failed with `"missing required column(s): signup_date_year,
signup_date_month, signup_date_dayofweek, ..."` — `prediction_service.predict_batch()`
did `rows_df[all_features]` directly, requiring a fresh upload to already contain a
trained model's internal DERIVED feature names, which no real raw file ever would.

**Fix applied**: new `ml_service.derive_date_features_for_prediction()` — the exact
inverse of `select_training_features()`'s date handling — reconstructs
`{col}_year/month/dayofweek` from the model's recorded `preprocessing_json` (raw column
name + which derived names it produced) if the raw column is present in the upload.
Wired into both `predict_batch()` and `predict()` before feature validation, so both
batch and single-row prediction only ever require the columns a real user actually has.

Verified against the real files: `new_customers_to_predict.csv` (500 rows, raw
`signup_date` string) now predicts successfully — 236 predicted returners vs. true 223
(within the spec's 190-260 allowed range) — and this was only reachable at all once
Finding 8 (orders_next_90_days leakage exclusion) also landed, since the batch file
naturally lacks that future-only column.

## Finding 12 — telecom clustering picks k=5, not 4 — ROOT-CAUSED, not fixed (Phase 3 scope)

`call_minutes_month` has exactly 4 values (45000, 52000, 60000, 71000) against a normal
range of mean=462/std=1876 (max otherwise in the low thousands) — a month has at most
44,640 minutes total, so these 4 values are **physically impossible**, not merely
statistically rare, matching the spec's "possible fraud" framing exactly.

Verified causally (not just by inspection) — computed silhouette scores with and without
these 4 rows, using the exact same feature set and KMeans/silhouette code
`run_clustering_analysis()` uses:

```
WITH fraud rows:    k=2..6 silhouette = [.330, .406, .424, .431, .358] -> best k=5
WITHOUT fraud rows: k=2..6 silhouette = [.367, .423, .437, .364, .297] -> best k=4
```

Removing the 4 rows flips the silhouette-optimal k from 5 to 4 — confirming they distort
the cluster structure, not that the model was tuned to the answer key's k=4 (the
"physically impossible" bound was established from the data's own physical limits before
this verification, not reverse-engineered from the answer key).

**Why this isn't fixed now**: excluding these values generally (not as a
`telecom`-specific hardcoded `40000` constant) requires the plausibility-range /
implausible-large-value cleaning Phase 3 already specifies (parallel to the existing
rare-negative-value check, generalized to an upper bound too) — a bigger, shared
mechanism, not a one-off patch in `run_clustering_analysis()`. Documented here so Phase 3
picks this up as a concrete, pre-verified test case rather than starting from scratch.

## Phase 2 — role-based Data Understanding merge (2026-09-26)

Scope for this round (per explicit direction): fix Finding 10's post-outcome detection
properly as a column ROLE in `data_understanding_service`, independent of whichever
target is chosen, and confirm `complaints_last_6_months` stays un-flagged. The broader
Phase 2 scope (new roles, unit tracking, Gemini tie-breaker, full target-scoring merge)
remains open — see "Next step" — deliberately deferred, not required for this goal.

### Finding 13 — `detect_dataset_type` misclassified every customer-level dataset with a signup date — ✅ FIXED

`customer_churn_dataset.csv`, `customer_retention_training.csv`, and
`telecom_subscribers_usage.csv` all have a customer identifier with (nearly) one row per
customer — textbook `customer_level` — but all three were classified `general` instead.
Root cause: the `customer_level` branch required `not has_timestamp`, so a signup/
activation date (a per-customer ATTRIBUTE) incorrectly disqualified it, as if any
timestamp column implied a per-event/per-period log.

**Fix applied**: `detect_dataset_type()` now checks a customer identifier's
rows-per-customer ratio FIRST, before any timestamp-based branch. Verified against all 6
real datasets: churn/retention/telecom now correctly report `customer_level`;
retail/karachi (no customer_id column at all) stay `time_series`, unaffected; coffee_shop
stays `transaction_log`, unaffected. New regression tests in
`tests/test_data_understanding_roles.py`.

### Finding 14 — post-outcome detection rebuilt as a column ROLE, not a correlation-vs-target heuristic — ✅ FIXED

Per Finding 10's disclosed limitation (a genuine leak and pure statistical noise can both
show under 1% correlation — no threshold can tell them apart), `detect_post_outcome_features()`
is replaced entirely. The new `data_understanding_service.detect_post_outcome_columns()`
uses a target-INDEPENDENT structural signal instead: the same post-outcome word
(tip/rating/review/refund/complaint/late/cancelled/return/...) means "this row's own
reaction" in a `transaction_log`/`time_series` dataset (one row per event, verified via
Finding 13's corrected rows-per-identifier check) but "an accumulated historical
attribute" in a `customer_level`/`general` dataset (one row per entity) — and must only
fire in the former.

A nuance found and fixed while verifying against real data: karachi's `rider_rating` (the
rider's own pre-existing reputation, legitimately known in advance) matches "rating" by
name just like `customer_rating` (this delivery's own post-hoc feedback) — both live in
the same `time_series` dataset, so the structural gate alone doesn't separate them. Added
a narrow, separately-justified vocabulary: a REPUTATION word (rating/review/feedback/
satisfaction/resolved) paired with a SERVICE-PROVIDER word (rider/driver/courier/agent/
seller/vendor/staff) names that provider's own track record, not a reaction to this row —
`tip`/`refund`/`late`/`cancelled`/`complaint`/`return` are deliberately NOT covered by
this exception, since a rider's tip is still only known once THIS delivery concludes
regardless of whose perspective it's framed from.

Since there's no longer a per-column correlation signal, the old "confirmed hard leak vs.
merely reviewed" split (based on being a statistical outlier among name-matched peers) is
gone — every flagged column is now uniformly excluded-by-default-but-overridable (the
existing `post_outcome_excluded` / `include_post_outcome_features` checkbox flow),
since that distinction was itself built on the same unreliable correlation evidence.

Also fixed while wiring this in: `detect_leakage()` had an asymmetric blind spot — its
feature-side correlation coercion (`pd.to_numeric`) silently skipped any BINARY
CATEGORICAL feature (e.g. `late_delivery`: Yes/No) against a NUMERIC target, since
`_abs_correlation`'s existing binary-factorize fallback was never applied to
`detect_leakage`'s own loop. A real test (`test_late_delivery_style_categorical_leak_against_numeric_target_is_caught`)
depended on this ONLY working by accident, via the old post-outcome mechanism's
correlation check. Fixed properly: `detect_leakage` now (a) factorizes a binary
categorical feature the same way `_abs_correlation` already does, and (b) for such a
feature, also checks whether it agrees with comparing the TARGET against another numeric
feature (the same "does a > b agree with a boolean" technique `detect_pairwise_leakage`
already uses between two features, applied here between the target and one feature) —
catching `late_delivery = delivery_time_min > promised_time_min` when `delivery_time_min`
is itself the (continuous) target.

Verified: 7 new tests in `test_data_understanding_roles.py` (dataset-type reclassification
+ post-outcome role, run directly against all 6 real CSVs, not just synthetic fixtures),
plus `test_complaints_last_6_months_not_flagged_as_post_outcome_in_real_telecom_data` and
`test_customer_rating_and_returned_flagged_as_post_outcome_in_real_retail_data` as direct,
fast unit tests (no full API round-trip needed) pinning the exact case that motivated the
redesign. 4 existing `test_target_and_leakage.py` tests updated (their synthetic fixtures
needed a date column to be `time_series`-shaped, and 2 tests were rewritten to test the
new structural distinction directly — same word, different dataset shape, different
outcome — rather than the old, now-obsolete "weak correlation" premise).

### Finding 15 — full benchmark after Phase 2: 61/64 (up from 60/64)

```
customer_churn_dataset.csv:        10/10 PASS
retail_sales_dataset.csv:           9/11 PASS  (customer_rating AND returned both now
                                                 correctly excluded — Finding 10 resolved)
karachi_food_delivery_dataset.csv: 12/12 PASS  (rider_rating correctly stays included)
customer_retention_training.csv:    8/8  PASS
coffee_shop_transactions.csv:      15/15 PASS
telecom_subscribers_usage.csv:      7/9  PASS  (complaints_last_6_months confirmed safe)

61/64 checks passed — 6 dataset(s) run, 0 skipped, 0 errored
```

Full backend test suite: **250/250 passing** (243 prior + 7 new
`test_data_understanding_roles.py` tests; 3 pre-existing tests fixed for the redesign —
`test_post_outcome_named_feature_excluded_by_default_and_overridable` needed a date
column, `test_auto_analyze_end_to_end_on_the_real_karachi_dataset` needed the
rider_rating/customer_rating distinction, `test_late_delivery_style_categorical_leak_against_numeric_target_is_caught`
needed the `detect_leakage` binary-feature fix above).

The 3 remaining failures are the same pre-existing, already-diagnosed gaps from the Phase
1 baseline (Nov-Dec report gap, telecom k=5 — Finding 12, next up in Phase 3 — and the
per-row cluster API gap) — `customer_rating` is no longer among them.

## Phase 3 — plausibility-range cleaning (2026-09-26)

Directly motivated by Finding 12 (telecom's 4 physically-impossible call-minute values
distorting clustering into k=5 instead of k=4). Depended on Phase 2's column roles being
in place first, since knowing which columns a plausibility bound even applies to needs
the same evidence-first classification work.

### Finding 16 — plausibility-range cleaning added, deliberately restricted to two universal cases — ✅ FIXED

A generic "far from the statistical bulk of the distribution" upper-bound test was tried
FIRST and rejected — probed against all 6 real datasets before writing any production
code, it also flagged legitimately rare (not impossible) values with no hard ceiling: a
$482 monthly spend in churn, a 144-unit bulk order in retail, a 354-minute delivery in
karachi. Nulling these would have been a real regression (a genuine high-value customer
or big order silently corrupted), not a fix.

Instead, `cleaning_service.py` gained two narrow, genuinely dataset-agnostic plausibility
checks — each requires a real, checkable domain ceiling, never a per-dataset guess:
  - **Age**: any column tokenized as `age` cannot exceed 120 (the oldest medically
    verified human age is ~122) — a near-universal biological fact.
  - **Time-quantity-within-a-period**: a column naming a time UNIT (minute/hour/...)
    within a time PERIOD (day/week/month/year) — e.g. `call_minutes_month` — cannot
    exceed that period's actual maximum duration in those units (a month has at most
    31 x 24 x 60 = 44,640 minutes, computed from the name, not hardcoded per dataset).

Both mutate the same way `_replace_impossible_negative_values` already does (converts the
implausible value to missing, then the standard fill step imputes it — never guesses a
replacement directly) and report the same way in the quality report (`impossible_values`
type, `review_only` recommended fix).

Verified against real data: telecom's `call_minutes_month` (45000/52000/60000/71000) and
`age` (134) are now genuinely corrected, not just statistically flagged; retention's
`age` (150, 212) likewise. Full backend suite: 250/250 (no regressions — verified the
generic-statistical-test alternative would have broken churn/retail/karachi before ever
writing it into production code, not after).

**Known, disclosed limitation**: telecom's age=7 and retention's age=0 are NOT caught —
both are below the normal adult range but not above any universal ceiling, and there is
no general, non-hardcoded rule that catches exactly these two without also risking a
false positive elsewhere (verified: a percentile-gap statistical test that catches these
also catches the legitimate high-value cases above). Catching them would need either a
domain-specific "minimum customer age" constant (which would be tuning to this dataset's
specific numbers, not a general rule) or a more sophisticated gap-density statistical
technique not yet built. Documented honestly rather than force-fit.

### Finding 17 — Finding 12's root cause fixed and verified; exact k=4 target still not hit — reported honestly, not force-tuned

With the 4 fraud values now genuinely corrected (nulled + median-imputed, not just
flagged), the demonstrated distortion is gone — but the real pipeline's clustering
now selects **k=3** (silhouette 0.4426), not k=5 (the bug) OR k=4 (the answer key's
implied segment count).

Investigated rather than left unexplained: a standalone probe (dropping the 4 fraud
ROWS entirely, on a 10-column feature subset) had shown k=4 as optimal — but the REAL
pipeline's feature set has 11 columns (it correctly includes `data_usage_gb_month`,
converted from `"75.3 GB"`-style strings during cleaning, which the standalone probe
omitted) and NULLS-then-MEDIAN-IMPUTES the 4 values rather than dropping their rows
(consistent with every other implausible-value correction in this codebase). Reproduced
end-to-end through the real API path to confirm this precisely rather than guessing.

**Why this isn't chased further**: silhouette-based k-selection is legitimate,
principled unsupervised model selection — it does not need to reproduce a specific
answer key's segment count, and hand-picking which features to include/exclude, or
retuning the silhouette search, specifically to land on k=4 would be exactly the
"tuning methods to the answer keys" this task's own rules forbid. The concrete, provable
bug (4 impossible values inflating k to 5) is fixed and verified; the remaining gap to
k=4 is reported honestly rather than closed by fitting the answer key.

Full benchmark: **61/64** (unchanged from Phase 2's count — same 3 pre-existing gaps,
just telecom's k mismatch is now 3-vs-4 instead of 5-vs-4). Full backend suite: 250/250.

### Finding 18 — `run_clustering_analysis()` had 3 real methodology flaws, not just a stubborn k — ✅ FIXED (2026-09-26)

Finding 17 concluded k=3-vs-4 shouldn't be chased further to avoid tuning to the answer
key. That conclusion was correct GIVEN the scope investigated at the time (feature-set
completeness and null-vs-drop handling) — but a deeper, user-directed investigation into
standard segmentation methodology (never looking at the answer key to decide what to fix)
found the clustering function itself had three real, independently-justifiable flaws:

1. **Demographics were forming clusters, not describing them.** `age` was included as a
   clustering input alongside behavioral/usage columns. Standard segmentation practice
   clusters on behavior and profiles the result with demographics afterward — a
   customer's age doesn't change which behavioral segment they're in, it explains one
   once membership is already decided.
2. **No log-transform for skewed usage columns.** Usage/count metrics (call minutes, GB,
   SMS) are almost always right-skewed (most users use a little, a few use a lot); on the
   raw scale, those few extreme values dominate the Euclidean distance KMeans/GMM are
   built on.
3. **k chosen from silhouette alone.** A single statistical measure can be misled by
   whatever happens to be in the feature set — this is literally what picked k=5 for the
   fraud-distorted data.

**Fix applied**: `_clustering_feature_columns()` splits numeric columns into behavioral
(clustering inputs) vs. descriptive (age, plus any derived-date-part column — checked by
verifying the base column is itself a TIMESTAMP role, not a naive name-suffix guess, see
below) — only `age` needed an explicit rule, since other demographics (city, gender, plan
type) are already categorical and were never in the numeric feature set to begin with.
`_log_transform_skewed()` applies `log1p` to any column with sample skew > 1 (a standard
statistical convention), skipping any column with a negative value. `k` is now chosen by
`_select_k_by_combined_score()`: for k=2..8, computes silhouette, Gaussian Mixture BIC,
and bootstrap-resample stability (Adjusted Rand Index between a reference clustering and
repeated resample refits — a standard reproducibility check), min-max normalizes all
three, and averages them. None of this ever reads the answer key.

**A real bug found and fixed while building this**: the first implementation's
derived-date-part exclusion used a naive `col.endswith(("_year","_month","_dayofweek"))`
check — this wrongly caught genuine usage columns that merely happen to end in `_month`
(`call_minutes_month`, `data_usage_gb_month`, `sms_month`, `international_minutes_month`),
shrinking the real feature set from 10 behavioral columns to 4 and producing a nonsense
k=6. Caught by inspecting the actual `features_used` output before accepting the result,
not by tuning against the answer key — fixed by requiring the SUFFIX-STRIPPED base column
to itself be classified `ROLE_TIMESTAMP` before treating a `_month`/`_year`/`_dayofweek`
column as date-derived (this currently never triggers for any of the 6 real datasets,
since none of them derive date-part columns before clustering — a defensive rule for a
future caller, verified not to misfire on the real data).

`run_clustering_analysis()` also now exposes `predictions` (per-row `{id, cluster}`,
joined on the dataset's customer_id/identifier column when one exists) — closing the
Phase 4/5 "no per-row cluster assignments via the API" gap `checks_telecom.py` had been
waiting on — and `k_selection` (every candidate k's silhouette/BIC/stability/combined
score, for transparency/audit) and `descriptive_summary` per cluster (the EXCLUDED
demographic/categorical columns' cluster-conditional means/distributions — age, city,
gender, plan type, device tier — the "describe afterward" half of the methodology).

**Real result on telecom** (verified end-to-end through the real API, not a standalone
script):

| k | silhouette | GMM BIC | stability | combined |
|---|---|---|---|---|
| 2 | 0.314 | 72579 | 1.000 | 0.391 |
| 3 | 0.419 | 70341 | 0.998 | 0.668 |
| **4** | **0.427** | **62875** | **1.000** | **0.755** |
| 5 | 0.376 | 61284 | 0.974 | 0.637 |
| 6 | 0.316 | 57149 | 0.981 | 0.530 |
| 7 | 0.291 | 40633 | 0.983 | 0.618 |
| 8 | 0.297 | 35913 | 0.958 | 0.668 |

k=4 wins the combined score outright. `features_used` = the 10 genuinely behavioral
columns (`data_usage_gb_month`, `call_minutes_month`, `sms_month`,
`night_data_share_pct`, `international_minutes_month`, `social_media_share_pct`,
`video_streaming_hours_month`, `roaming_days_last_year`, `avg_monthly_recharge_pkr`,
`complaints_last_6_months`); `descriptive_features` = `['age']`; 6 of the 10 behavioral
columns were log-transformed.

**Diagnostic only, computed strictly AFTER k was chosen**: match rate against
`answer_key_true_segments.csv` = **99.9%** — strong independent evidence the methodology
fix was correct, not a coincidence, since the answer key played no role in choosing k,
the feature split, or the transform.

Full benchmark: **63/64** (up from 61/64 — both telecom checks now pass). Full backend
suite: **250/250**. The one remaining failure is unrelated to clustering: retail's
Nov-Dec seasonal-peak narrative (pre-existing Phase 4/6 report-architecture gap).

## Phase 4 — unified analysis planner (2026-09-26)

Root cause #4's "forced/narrow analysis path" concern and Phase 0 Finding 1's documented
duplication: before this phase, the decision of which analyses a dataset supports was
split three ways — `dataset_service.score_target_candidates()` decided supervised
viability inline in `auto_analyze_service`, `ml_service.run_clustering_analysis()`'s own
internal checks decided clustering viability (discoverable only by attempting it, no
pre-execution estimate at all), and `transaction_analysis_service.build_analysis_plan()`
was a SEPARATE, transaction-log-only planner that was the only one of the three that
actually produced an explicit, user-facing plan object.

### Finding 19 — one shared planner now used by every Auto Analyze run — ✅ FIXED

New `app/services/planning_service.py`, `build_analysis_plan()` — the single entry point
every Auto Analyze run calls right after Data Understanding/target-candidate scoring:
  - For a `transaction_log` dataset: delegates to `transaction_analysis_service`'s
    existing, already-tested viability math for revenue/RFM/return-prediction/forecasting
    entries, rather than duplicating it.
  - For every other dataset type: produces `supervised_prediction` (viable iff
    `score_target_candidates` found a candidate; includes the suggested target) and
    `segmentation` (viable iff enough rows and behavioral numeric columns exist — a
    cheap pre-execution ESTIMATE, never overriding what `run_clustering_analysis()`'s
    own real execution actually decides).

**Deliberately scoped narrower than a full control-flow rewrite**: the plan is now
computed and stored (`job.result_json["plan"]`) for EVERY dataset type — previously only
transaction-log jobs had a `plan` key at all — but the actual EXECUTION dispatch (target-
confirmation pause vs. clustering fallback vs. transaction-log plan-confirmation pause)
and the working, heavily-tested pause/resume UX are UNCHANGED. A transaction-log dataset
still isn't ALSO scored for a supervised target — that mutual exclusivity is an existing,
already-validated design decision, not something this phase's scope required revisiting.
Unifying the three EXECUTION engines themselves (`ml_service.train_models`,
`ml_service.run_clustering_analysis`, `transaction_analysis_service.run_full_transaction_analysis`)
into one is a materially bigger, higher-risk change than unifying the DECISION of which
to use — the latter is what root cause #4 and Finding 1 actually describe as duplicated.

Verified: full backend suite 250/250 (no regressions — the planner is purely additive:
existing branches still work exactly as before, just also produce a stored plan now).
Full benchmark: **63/64**, unchanged from the segmentation-methodology fix — the planner
integration touches no scoring/execution logic, only adds a transparency layer.

### Finding 20 — plan confirmation gap closed: reasons, confidence levels, mandatory confirmation, and an override — ✅ FIXED (2026-09-26)

A direct audit (before Phase 5) of Phase 4's planner against 4 explicit requirements —
show the plan with reasons, show a confidence level, require confirmation when confidence
is low, let the user change it — found 3 real gaps:

1. **No confidence level anywhere.** Every plan entry had `viable: bool` but nothing
   describing HOW confident that estimate was.
2. **Segmentation never paused for confirmation.** Supervised and transaction-log plans
   already always pause; segmentation (`_run_general_analysis`) ran straight through with
   zero visibility, regardless of confidence — so a low-confidence segmentation call could
   execute completely unseen, the literal failure mode requirement #3 exists to prevent.
3. **No way to change a segmentation or transaction-log plan.** Only the supervised path
   let the user pick a different target; `confirm-plan` took no body at all.

**Fixed**:
  - `planning_service.py`: `_score_confidence()` buckets a target-candidate's raw score
    into high/medium/low (calibrated against real scores observed across the 6 benchmark
    datasets — a bare name-hint match scores 3-5, combined with a real cardinality-shape
    match, 5-8); a close runner-up candidate (within 1.0 of the top score) downgrades
    confidence one band, since a near-tie is itself evidence of ambiguity a raw score
    alone hides. Segmentation's confidence comes from how far above the minimum viable
    row/column counts the real numbers are. Every entry also gets a `reason` string. An
    overall `plan["confidence"]` reports the PRIMARY analysis's confidence.
  - `transaction_analysis_service.build_analysis_plan()`: the same treatment for
    revenue_analytics/customer_rfm/return_prediction/sales_forecasting, confidence banded
    on the margin above each analysis's existing viability threshold.
  - `auto_analyze_service.py`: the `if not candidates:` branch now sets
    `AWAITING_PLAN_CONFIRMATION` instead of calling `_run_general_analysis()` directly —
    segmentation is confirmed exactly like the other two paths now, EVEN when nothing is
    viable at all (giving the user a chance to override rather than landing on an EDA-only
    report with no say in the matter). `confirm_plan_and_resume()` gained an optional
    `target_column_override` parameter: if given, discards the plan and runs supervised
    training on that column instead (the "let the user change it" requirement) — a single,
    shared override mechanism now serves BOTH the segmentation and transaction-log paths.
  - `schemas/auto_analyze.py` / `api/auto_analyze.py`: new `ConfirmPlanRequest` with an
    optional `target_column`; the endpoint parameter needed a default instance
    (`ConfirmPlanRequest()`), not just an optional field, since FastAPI otherwise still
    requires SOME request body to be present — caught by the existing coffee-shop test
    suite, which calls `/confirm-plan` with no body at all (confirming the default stays
    backward compatible).

**A real, pre-existing bug found while verifying this against real data** (not something
this session introduced): generating an example plan for `coffee_shop_transactions.csv`
reported "27028 day(s) of history" and "3861 week(s)" in the return-prediction/forecasting
reasons — nonsensical for a dataset spanning about 15 months. Root cause: a planted
invalid date (`2099-01-01`, one of the dataset's known 6 invalid dates) was included in
`build_analysis_plan()`'s raw `parsed.max() - parsed.min()` span calculation, which had
never been filtered by `flag_invalid_dates()` the way actual execution
(`_run_transaction_log_analysis`) already does. The boolean `viable` flag was accidentally
still correct either way (both the real ~454-day span and the inflated ~27,000-day one
clear the 180-day threshold), which is exactly why this went unnoticed before — only
surfacing the actual number in a `reason` string exposed it. Fixed by applying the same
`flag_invalid_dates()` filter before computing span; corrected values: 454 days, 64 weeks.

**Verified example plans, all 6 real datasets** (via the actual API, not a synthetic
fixture):

| Dataset | Status | Leading analysis | Overall confidence |
|---|---|---|---|
| customer_churn_dataset.csv | `awaiting_target_confirmation` | supervised_prediction → `churn` | high |
| retail_sales_dataset.csv | `awaiting_target_confirmation` | supervised_prediction → `sales_amount` | high |
| karachi_food_delivery_dataset.csv | `awaiting_target_confirmation` | supervised_prediction → `late_delivery` | high |
| customer_retention_training.csv | `awaiting_target_confirmation` | supervised_prediction → `will_return_next_90_days` | high |
| coffee_shop_transactions.csv | `awaiting_plan_confirmation` | revenue_analytics + customer_rfm + return_prediction + sales_forecasting, all viable | high |
| telecom_subscribers_usage.csv | `awaiting_plan_confirmation` | supervised_prediction NOT viable (confidence: none) → segmentation (11 behavioral columns, 4000 rows) | high |

Note: the 4 datasets with a confident supervised target still pause at
`AWAITING_TARGET_CONFIRMATION` (the pre-existing, dedicated target-confirmation flow, not
`AWAITING_PLAN_CONFIRMATION`) — both flows now carry the same reason/confidence
information; they remain two distinct statuses because target confirmation's UI (a
column-picker) and plan confirmation's UI (a checklist of analyses) are different shapes,
not because one is more "confirmed" than the other.

Verified: 3 new checks added to `test_general_analysis.py` (plan structure, confidence
presence, segmentation now genuinely requiring confirmation before AND after a not-viable
case) and `checks_telecom.py` (plan-confirmation pause + reason/confidence presence) — all
pass. Full backend suite: **250/250**. Full benchmark: **66/67** (3 new checks, all
passing; same single pre-existing Nov-Dec gap as before).

## Next step

Phases 1-4 (including the Finding 20 plan-confirmation follow-up) and the segmentation-
methodology fix (Finding 18) are all done for this round's scope — full benchmark
**66/67**, full test suite **250/250**. Findings 6-20 are fixed, documented, or explicitly
deferred per user decision; none is a silent/unknown gap. The only remaining benchmark
failure is retail's Nov-Dec report gap, which is explicitly Phase 6 scope (see below).

## Phase 5 — modeling safety unification (2026-09-26)

Phase 0 Finding 2: `return_prediction_service.py` (the coffee-shop transaction-log path's
return-prediction model) had its own bespoke training code — fixed
`NUMERIC_FEATURES`/`CATEGORICAL_FEATURES` lists, never leakage-checked, no baseline
comparison, no weak-model warning — entirely separate from `ml_service.train_models()`'s
real, tested leakage detection, baseline, and weak-model flagging.

### Finding 21 — return-prediction now runs the same leakage/baseline/weak-model checks train_models() does — ✅ FIXED (partial scope, documented)

**Deliberately scoped narrower than routing return-prediction through `train_models()`
entirely.** That function assumes a raw, row-per-record dataframe with its own feature
selection; return-prediction's data shape is fundamentally different — a time-cutoff
train/predict split over CUSTOMER-LEVEL AGGREGATES already computed by
`customer_analytics_service.build_customer_table()` (recency/frequency/monetary/etc., one
row per customer). Rebuilding that on top of `train_models()`'s API would mean either
distorting `train_models()` to accept pre-aggregated input or duplicating its internals —
a bigger, higher-risk change than what this finding actually asks for: the SAME safety
CHECKS, not identical training orchestration.

**Fixed**:
  - Extracted `ml_service.compute_baseline_comparison()` out of the loop inside
    `train_models()` (previously inline, recomputed per candidate model) into a standalone,
    reusable function — `train_models()`'s own behavior is unchanged (verified: the exact
    same computation, just callable from elsewhere).
  - `return_prediction_service.run_return_prediction()` now calls `ml_service.detect_leakage()`,
    `detect_pairwise_leakage()`, and `detect_derived_metric_features()` against its 7
    candidate features before training — any flagged feature is excluded from that run
    (mirroring `resolve_training_features`'s exclude-and-continue behavior), verified
    against real data to find NONE (see below) — this was a real, unverified assumption
    before (the feature list "looked safe" by construction — pre-cutoff aggregates can't
    literally see the future — but was never actually checked the way every other trained
    model in this app is).
  - Added cross-validation (previously absent entirely) and `compute_baseline_comparison()`
    + `evaluate_model_strength()` — the same standard every other model in this app is
    held to, applied here for the first time.
  - `_build_pipeline()` now takes the (possibly leakage-reduced) feature lists as
    parameters instead of reading fixed module constants, so an exclusion actually takes
    effect rather than the pipeline silently continuing to use the excluded column.

**Verified against real data** (`coffee_shop_transactions.csv`, through the real API, not
a synthetic fixture): `leakage_warnings=[]` (confirms genuine safety, not just assumed),
`features_used` unchanged (all 7 original features), baseline comparison `{baseline_score:
0.538, model_score: 0.929, clearly_beats_baseline: True}`, `is_weak: False`. The actual
prediction output is UNCHANGED (705 of 1150 predicted returners, matching the
already-passing benchmark check against the answer key) — this was purely an added
verification/reporting layer, not a change to the model or its predictions.

**Remaining, explicitly NOT done** (Phase 0 Finding 2's other half): return-prediction
still doesn't save an `MLModel` row via `prediction_service`, so there's no batch-
prediction UI path for it — a customer-return-risk model can't be reused the way a
`train_models()`-trained model can. This needs a real design decision (what would a batch
prediction FILE even look like for a model trained on aggregated, not raw, features?) more
than a mechanical wiring change, and is left open rather than half-implemented.

Full backend suite: **250/250** (no regressions — `train_models()`'s own tests confirm the
baseline-extraction refactor didn't change its behavior). Full benchmark: **66/67**
(unchanged — this finding only added verification/reporting, not a behavior change to
what the benchmark checks).

## Phase 6 — adaptive report (2026-09-26)

Phase 0 Finding 3: report sections were a hardcoded `{% if model %}...{% elif
clusters %}...{% elif transaction_analysis %}...` branch chain in `report.html`, not
driven by the plan Phase 4 now computes for every dataset — plus a set of concrete report
defects surfaced by earlier reviews (hardcoded cross-dataset examples, meaningless summed
KPIs, no currency formatting, invalid dates leaking into date ranges/trend charts, refunds
mislabeled as errors, recommendations that were just a restated feature-importance table,
thin segmentation output, raw internal fields leaking into the PDF appendix, cover page
spilling onto page 2).

### Finding 22 — nine concrete report defects, each fixed and verified against real data — ✅ FIXED

1. **Business question always derived from the plan, never the free-text project
   description.** Removed the branch in `report_context_service.build_report_context()`
   that overwrote the derived business question with `dataset.description` whenever one was
   present. The business question is now always the elif-chain result based on what was
   actually analyzed (transaction_analysis → revenue/RFM/return/forecast question; model →
   "What predicts {target}?"; clusters → "What natural groupings exist?"). The raw
   free-text description, if any, is now shown separately and unambiguously labeled
   "Project description (as entered by the user):" so it's never mistaken for the derived
   question.
2. **No hardcoded cross-dataset examples.** `cleaning_service._strip_units_from_numeric_like_columns()`
   now captures a real example value per column (e.g. actual matched text, not the
   hardcoded "4.2 km"); the text-case-normalization path now surfaces a real
   variant/canonical pair from the actual data instead of the hardcoded "'rainy' vs
   'Rainy'".
3. **Meaningful KPIs per dataset type.** `eda_service._select_kpis()` now takes the real
   column roles and only SUMS columns with a genuine MONEY or QUANTITY role ("Total X");
   everything else (age, rating, price-per-unit) is AVERAGED ("Average X") instead of
   nonsensically summed. A genuine percentage-shaped column is detected and shown as a
   percent rather than a raw number. New `app/utils/formatting.py` (`format_money`,
   `detect_currency_from_column_name`) abbreviates large numbers (13,100,000 → "13.1M")
   and prefixes a currency code ONLY when genuinely evidenced by a column name (e.g.
   "total_pkr" → PKR) — never guessed. Registered as the Jinja `money` filter.
4. **Invalid dates excluded from date ranges and trend charts.** Both the dataset-section
   `time_period` and `eda_service._select_time_trend()` now filter through
   `cleaning_service.flag_invalid_dates()` before computing min/max/grouping. Revenue
   trend narratives now name the real peak month by computing a monthly SUM (not mean) of
   the target/business-outcome column and reporting `{month, value}` — this is also the fix
   for the one benchmark check that had failed since the harness was first built:
   **"Nov-Dec revenue peak visible."**
5. **Refunds described as refunds, losses described as losses.**
   `cleaning_service._detect_refunds()` now classifies each negative-capable column's
   "kind" as `"loss"` (name matches profit/margin/markup/commission/surplus/net) vs.
   `"refund"` (everything else), producing distinct, semantically-correct messages instead
   of a single generic "error" framing for both.
6. **Recommendations are real business actions, not restated feature importances.** New
   `report_context_service._build_model_recommendations()` produces genuinely
   action-oriented text: gather-better-data advice for a weak model, an operational
   "lever" framing for the top regression feature, a highest-vs-lowest observed-rate
   segment call-out for classification, and a baseline-grounded confidence statement.
   Segmentation and transaction-log reports already had rule-based recommendations from
   earlier phases; those are unchanged. AI-generated recommendations remain separately
   badged per item and never appear unbadged.
7. **Segmentation reports now show segment names, sizes, distinguishing features, and
   suggested offers.** `_build_clusters_section()` rewritten to compute, per cluster: a
   rule-based name from the top 1-2 most-deviating behavioral features vs. the overall
   weighted average, a distinguishing-features table (value / overall value / % difference),
   the existing (previously computed but never shown) descriptive summary, and a rule-based
   suggested offer ranked by a detected money-role behavioral column when one exists
   (highest-value cluster → VIP/retention, lowest → win-back, middle → upsell) or by the
   single most distinctive feature otherwise.
8. **Transaction-report naming and peak-time fixes.** `customer_analytics_service.RFM_SEGMENTS["at_risk"]`
   renamed from "At Risk" to "Slipping Away" so it no longer collides with
   `return_prediction_service`'s independently-computed "At risk of not returning" group —
   two different things that previously looked identical. New
   `transaction_analysis_service._peak_hour_range()` reports the contiguous window of
   hours around the peak (e.g. "6 PM–10 PM") instead of a single hour.
9. **Formatting cleanup.** The appendix "Full model metrics" table now filters on the
   canonical metric-label registry instead of `v is number or v is string`, which had been
   leaking the internal boolean `is_weak` into the PDF as "Is Weak" / "False". MAPE
   formatting was checked and was already correct (already percentage-scaled at the
   source). The "no AI badge on failed AI sections" requirement was checked and was already
   correct (the badge CSS class exists but nothing renders it when AI is unreachable).
   Cover-page pagination was checked by actually rendering a PDF and counting pages with
   `pypdf` — a real bug: `margin-top: 220px` plus title/subtitle/meta lines pushed the
   cover onto page 2 for a longer title. Fixed by reducing cover margins/font-size; verified
   by re-rendering (page count dropped 19→18, cover content confirmed entirely on page 1).

**Two additional real bugs found and fixed along the way** (not explicitly requested, but
surfaced while verifying the above against real data):
  - Coffee shop's plan reported "27028 days of history" from a planted invalid
    `2099-01-01` date never filtered out of `transaction_analysis_service.build_analysis_plan()`'s
    span calculation (the actual execution path already filtered it; the plan-preview path
    didn't). Fixed by applying `flag_invalid_dates()` before computing the span. Corrected
    to 454 days / 64 weeks.
  - The live (non-test) dev server process had been running since before these changes and
    was started without `--reload`, so its in-memory Jinja `Environment` never picked up the
    new `money` filter registration — every live report generation crashed with
    `jinja2.exceptions.TemplateAssertionError: No filter named 'money'` even though the
    isolated benchmark harness (which imports the app fresh per test run) passed 67/67.
    This was caught only by generating real reports against the actually-running server, not
    by the test suite or benchmark harness alone. Fixed by restarting the server process; no
    code change was needed.

**Verified end-to-end in the live running app** (not just the isolated benchmark harness):
created 6 new projects against `http://localhost:8000`, uploaded each of the 6 real
benchmark datasets, ran Auto Analyze through to a generated report for each, and inspected
the actual rendered HTML preview (`GET /api/reports/{id}/preview`) for every dataset:
  - `customer_churn_dataset.csv` → business question "What predicts Total Revenue, and how
    reliably?" (plan-derived, not the project description).
  - `retail_sales_dataset.csv` → "Peak month: **November 2024** (Sales Amount totaled 69.0K
    that month, the highest of any month in the covered period)." — real, computed value.
  - `karachi_food_delivery_dataset.csv` → completed, plan-derived business question.
  - `customer_retention_training.csv` → PKR-formatted money values present.
  - `coffee_shop_transactions.csv` → "Peak revenue window is 6 PM–10 PM (PKR 5.6M in that
    single busiest hour)"; "Refund rate is 0.06% of transactions — low, with no sign of a
    widespread product or service-quality issue."; MAPE shown as a percentage.
  - `telecom_subscribers_usage.csv` → 4 real, distinct segment names (e.g. "High
    International Minutes Month, Low Video Streaming Hours Month — 577 rows (14.4%)") each
    with its own suggested offer (VIP/retention, upsell, win-back).
  - Across all 6: no `is_weak`/raw-field leaks, no hardcoded "4.2 km" / "'rainy' vs 'Rainy'"
    text, zero unbadged "AI-generated" mentions (consistent with AI being unreachable in
    this environment — Gemini rate-limited, Ollama not running — the report correctly fell
    back to rule-based content throughout).

Full backend suite: **250/250**. Full 6-dataset benchmark: **67/67** — every check now
passes, including the previously-failing "Nov-Dec revenue peak visible."

## Next step

## Phase 7 — AI reliability (2026-09-26)

Phase 0 Finding 4: the DB-persisted `AIInsightCache` table was only ever written to by
the AITab's own `/api/ai/insights` endpoint — and that endpoint's context-builder only
knew about a trained model, never clustering or transaction-log results. Separately, the
Auto Analyze pipeline's OWN baked-in AI narrative (`job.result_json["ai_insight"]`,
generated once per run for all three analysis types) never touched `AIInsightCache` at
all, and its "Retry" button (`retry_ai_insights()`) checked for `best_model_id` and
silently returned early when absent — meaning clustering and transaction-log jobs' Retry
button did *nothing* if the AI provider was unavailable during the original run: no
error, no retried call, no way to get a narrative afterward short of re-running the
entire pipeline.

### Finding 23 — Retry and the persistent insight cache now cover all 3 analysis types — ✅ FIXED

**Fixed**:
  - `context_service.py` gained `build_cluster_context()` and `build_transaction_context()`
    — the same shape `build_model_context()` already provided for supervised models — and
    `compute_insight_cache_key()` gained a `kind` parameter ("model"/"clustering"/
    "transaction_log") so a dataset with no model can't collide two DIFFERENT narratives
    (clustering vs. transaction-log) onto the same "no-model" cache key.
  - `auto_analyze_service.retry_ai_insights()` rewritten to dispatch on the job's
    `analysis_type` and rebuild whichever context that type actually used (via the shared
    `build_*_context` helpers, deduplicating what was previously three separate inline
    `ai_context` dicts across `_continue_training`/`_run_transaction_log_analysis`/
    `_run_general_analysis`) — clustering and transaction-log jobs can now genuinely be
    retried, not silently no-op'd.
  - All three original Auto Analyze code paths (supervised/clustering/transaction-log) and
    the retry path now persist a successful narrative to `AIInsightCache` via a shared
    `_persist_insight_cache()` helper — the same DB-backed cache, for every analysis type.
  - `/api/ai/insights` and `/api/ai/insights/cache` (the AITab's Generate/Regenerate flow)
    now look up the dataset's latest completed Auto Analyze job and build cluster/
    transaction-aware context for it — previously they silently built a model-only,
    cluster-and-revenue-blind context for these two analysis types even though the UI
    offered the button.

**Verified**: 4 new tests directly exercise the previously-dead paths — retrying a
clustering job's AI step and a transaction-log job's AI step both now actually invoke the
AI provider and persist a correctly-keyed cache row (previously they'd have silently
returned without calling anything); a `compute_insight_cache_key` test confirms
clustering/transaction-log/model narratives for the same model-less dataset get distinct
keys; an endpoint test confirms `/api/ai/insights` builds real cluster-aware context for
a clustering-only project. Full backend suite: **262/262** (258 prior + 4 new). Full
benchmark: **67/67** (unchanged — this phase only added retry/caching correctness, not a
behavior change to what the benchmark checks).

## Phase 8 — app consistency (2026-09-26)

Two explicit user requirements for this phase, plus the pre-existing "no pipeline
versioning" note from Phase 0.

### Finding 24 — Return-prediction UI (Finding 21's remaining gap) — ✅ FIXED

Transaction-log projects' return-prediction results (per-customer probability/risk group)
were already fully computed and stored (in `job.result_json["transaction_analysis"]
["return_prediction"]["predictions"]`) but had **no UI at all** — the Predictions tab only
ever checked for trained `MLModel` rows, and return-prediction deliberately never creates
one (Finding 21's documented scope decision — the data shape is a time-cutoff split over
customer-level aggregates, genuinely different from `train_models()`'s raw-dataframe
assumption). A user with a completed transaction-log analysis saw "No trained models yet"
even though 1,150 real per-customer predictions existed.

**Fixed**:
  - New `auto_analyze_service.get_latest_return_predictions()` finds the latest completed
    transaction-log job for a project and returns its return-prediction dict.
  - New endpoints `GET /api/predictions/return-predictions/{project_id}` (JSON summary +
    per-customer list) and `GET /api/predictions/return-predictions/{project_id}/download`
    (`?format=csv|xlsx`, via `pandas`/`openpyxl` — both already dependencies, no new ones
    added) in `app/api/predictions.py`.
  - `PredictionsTab.tsx` now shows a "Customer Return Predictions" card (risk-group
    badges, a paginated table, CSV/Excel download buttons) instead of "No trained models
    yet" whenever a project has no model but does have a completed return-prediction
    analysis.

**Verified against real data**: live-app check against the real coffee-shop project (not
just synthetic test fixtures) — `total_customers=1150`, `predicted_will_return=705`
(matching the already-established benchmark answer-key comparison), CSV download
contained all 1,150 rows (1,151 lines including header), Excel download opened correctly
via `pandas.read_excel`. 6 new backend tests (JSON shape, CSV content, Excel content,
unknown-format rejection, 404s for no-analysis and another user's project) + frontend
`tsc --noEmit` and `npm run lint`/`npm test` all clean (pre-existing, unrelated lint
findings in `ModelingTab.tsx`/`PredictionsTab.tsx`'s original effect/`auth-context.tsx`
were confirmed pre-existing via `git stash`, not introduced by this change). **Not
independently verified in a browser** — no browser-automation tool was available in this
session; the API responses, TypeScript compilation, and existing frontend test suite were
all checked, but the rendered page itself was not visually inspected.

### Finding 25 — stale-server prevention — ✅ FIXED

The exact bug that caused a real problem during Phase 6 verification (a long-running dev
server process, started before a code change, silently kept serving OLD code with no
error until every report crashed) now has three layers of defense:

  - `backend/dev.py` — the new canonical way to start the dev server, always with
    `--reload` (`python dev.py`). README's local-dev instructions updated to reference it.
  - `GET /api/health` now includes `git_commit` (`app/utils/version.py`'s
    `get_git_commit()`, via `git rev-parse HEAD` against the repo root, `None` if
    unavailable — e.g. a Docker image built without `.git`). The Settings page's new
    "System" card shows it.
  - New `backend/scripts/check_live_server.py` compares a running server's reported
    commit (via `/api/health`) against the local working tree's `git rev-parse HEAD`,
    printing a clear warning (and exiting 1 when run standalone) on a mismatch. Wired into
    `tests/benchmark/run_benchmark.py` as a non-blocking, informational check — the
    benchmark itself always runs in-process against current code via `TestClient` (see
    `harness.py`) and can never be stale itself, but a separate long-lived dev server used
    for live/manual verification often IS running alongside it, and that's exactly the
    process that can go stale.

**Explicitly scoped narrower than "pipeline versioning"** (Phase 0's original, vaguer
note): this phase implements exactly the two things asked for — return-prediction UI and
stale-server prevention — not a general versioning scheme for the cleaning/analysis
pipeline itself. That broader idea remains open and undefined; nothing here should be
read as having addressed it.

Full backend suite: **262/262**. Full benchmark: **67/67**. Live-verified: `/api/health`
and the Settings page's commit display checked against the actual running dev server
(restarted via `dev.py`), confirmed to report the correct commit and match local HEAD via
`scripts/check_live_server.py`.

## Next step

Phases 1-8 are all done for this round's scope — full benchmark **67/67**, full test suite
**262/262**. Findings 6-25 are fixed, documented, or explicitly deferred per user decision;
none is a silent/unknown gap. Phase 9 (final verification) is the only phase left on the
original roadmap.

Remaining open, deliberately-deferred items (not required for this round's stated goal):
  - "Pipeline versioning" in the broader, undefined sense Phase 0 originally gestured at
    (a general version/fingerprint scheme for the cleaning+analysis pipeline itself, not
    just the git-commit-level staleness check Finding 25 added) remains open.

The BROADER Phase 2 scope remains open and deliberately deferred (not required for this
round's stated goal):
  - New roles: count (distinct from quantity), rate/percentage, free text, target
    candidate — only identifier/customer_id/timestamp/money/quantity/category/other
    exist today.
  - Per-column unit tracking (e.g. "km", "PKR", "%") — not tracked at all yet.
  - Merging ALL target-candidate scoring into `data_understanding_service` (this session
    only threaded formula-column awareness into the OLD `dataset_service` scorer, which
    is still fundamentally name-hint-first, not evidence-first, for every other signal).
  - The Gemini tie-breaker call for target selection (separate from the existing
    column-ROLE Gemini tie-breaker, which already exists).
  - Reconciling `detect_dataset_type()`'s internal labels
    (transaction_log/customer_level/time_series/general) with the spec's exact wording
    ("customer-level table with outcome" / "transaction log" / "time series" / "table
    with no outcome") — the Finding 13 fix corrected the CLASSIFICATION logic but the
    label strings themselves are still the old internal names.
  - `suggest_target_column()`'s formula-column blind spot noted above.
