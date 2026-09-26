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
| 1 — Benchmark harness | ✅ Done | All 6 datasets present and running: **60/64 checks pass** (2026-09-26). See "Full 6-dataset baseline" below for the exact table and the 4 remaining, individually-diagnosed failures. |
| 2 — Data Understanding | 🟡 Partial | Target-candidate scoring, single-threshold leakage, and post-outcome hints hardened this session (Findings 8-11) — but still living in `dataset_service`/`ml_service`, not yet merged into `data_understanding_service` as the single source of truth. Broader scope (new roles, unit tracking, Gemini tie-breaker) still open — see "Next step". |
| 3 — Cleaning | ⬜ Not started | Partially done in a prior task (`column_roles`-aware, backward compatible). Needs the plausibility-range work this phase specifies — concretely motivated now by Finding 12 (telecom fraud values). |
| 4 — Planner | ⬜ Not started | `transaction_analysis_service.build_analysis_plan()` exists for transaction logs only; no segmentation/prediction planner yet |
| 5 — Modeling safety | 🟡 Partial | Batch prediction now reconstructs date-derived features from raw uploads (Finding 11) — but `return_prediction_service.py` still bypasses `ml_service`'s safety entirely (Phase 0 finding #2, unchanged). |
| 6 — Adaptive report | ⬜ Not started | Report sections are hardcoded template branches (model/clusters/transaction_analysis), not truly plan-driven — see Phase 0 finding #3 |
| 7 — AI reliability | ⬜ Not started | Gemini service is unified and cached for supervised/clustering paths; transaction-log AI narrative does NOT use the same DB cache — see Phase 0 finding #4 |
| 8 — App consistency | ⬜ Not started | No pipeline versioning exists yet |
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

## Full 6-dataset baseline (2026-09-26, after Findings 8-12 below) — 60/64 checks pass

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
   quick-fixable bug. User decision (2026-09-26): leave as-is, revisit as part of the
   proper Phase 2 role-based merge.
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

## Next step

Phase 1 harness is done and working across all 6 datasets (60/64); Findings 6-12 (the
concrete bugs/gaps it surfaced) are fixed, documented, or explicitly deferred per user
decision. These are all genuine, narrow instances of Phase 2/3/5's stated scope, done
surgically in `ml_service.py`/`dataset_service.py`/`prediction_service.py` rather than as
a full `data_understanding_service`/cleaning merge. The BROADER Phase 2 scope remains open
and is NOT yet done:
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
    with no outcome").
  - `suggest_target_column()`'s formula-column blind spot noted above.

All 6 datasets are now available, so this is no longer blocked — the broader Phase 2
merge (or moving on to Phase 3's plausibility-range cleaning, motivated directly by
Finding 12) is testable against the full set immediately. Both are legitimate next steps;
neither is blocked on the other.
