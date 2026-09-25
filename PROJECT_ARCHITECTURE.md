# AI Data Scientist — Architecture & Spec Gap Audit

_Last updated: 2026-09-25, as part of the "Any Dataset → Complete Data Scientist" upgrade task, then extended the same day for the "Coffee Shop Transaction Analysis" task (Data Understanding step, transaction-log analysis pipeline, and 4 report-quality fixes from a prior review), then extended again the same day for a forecast-accuracy/risk-group-consistency/rule-based-recommendations follow-up._

## -1. Forecast accuracy + risk groups + rule-based recommendations (latest follow-up)

- `islamic_calendar_service.py` (new) — approximate Ramadan/Eid al-Fitr/Eid al-Adha Gregorian date windows, computed per Hijri year (not hardcoded) from the mean synodic Islamic year length (354.36667 days) projected from one well-established anchor date — an explicitly-documented astronomical approximation (±1-2 days), always used with a multi-day buffer.
- `forecast_service.py` (rewritten) — modeling granularity is now WEEKLY (monthly is a display-only aggregation, attributed by majority-of-days-in-month). Four candidate methods (naive, seasonal naive, Holt's linear trend, OLS regression with Ramadan/Eid dummies + active-customer-count driver) are compared via a walk-forward rolling backtest on the dataset's own history; the winner is selected purely by lowest backtest MAPE — this module never reads any answer-key file. Uncertainty bands come from the winning method's own out-of-sample backtest fold errors, not in-sample residuals. Result: mean absolute % error against the real answer key dropped from ~19.8% (old monthly-only Holt's) to ~6.5-7.1% (new weekly, backtest-selected method — Holt's won on this dataset, not the calendar-aware regression, an honest finding from the backtest itself).
- `return_prediction_service.py` — risk groups and the headline "predicted to return" count previously used two different thresholds (classifier's raw 0.5 decision vs. 0.6/0.35 band cutoffs) and didn't add up. Now a single set of named probability bands (`RISK_BAND_LIKELY=0.6`, `RISK_BAND_AT_RISK=0.4`) drives both — the headline count IS the "Likely to return" band count by construction, so `predicted_will_return + predicted_will_not_return + predicted_uncertain == total_customers` always holds exactly, and the bands themselves are returned (`risk_bands`) for the report to state explicitly.
- `transaction_analysis_service.generate_rule_based_recommendations()` (new) — deterministic, non-AI recommendations from already-computed real numbers (top revenue category, peak hour, refund-rate assessment, At-Risk/Lost segment size and value, loyalty-member vs. non-member predicted return-rate comparison). Always shown in the report; AI-generated recommendations (when available) are appended after them, each individually badged "AI" — never blended in unlabeled.

## 0. Transaction-log analysis pipeline (added this task)

New services, additive and non-destructive to the existing supervised/clustering/general Auto Analyze paths:

- `data_understanding_service.py` — classifies every column's business ROLE (identifier, customer_id, timestamp, money, quantity, category, other) and whether it can legitimately be negative, rule-based first with an optional Gemini cross-check (never sent raw rows, only names + summary stats); detects the dataset TYPE (transaction_log/customer_level/time_series/general) with a real rows-per-identifier check, not just column presence; detects FORMULA columns (e.g. `total = quantity * unit_price - discount`) so they're never chosen as prediction targets.
- `cleaning_service.py` extended (backward compatible via an optional `column_roles` param) — refunds (negative money/quantity in a transaction dataset) are preserved and reported as refunds, never wiped to NaN by the old "rare negative = data-entry error" heuristic; missing `customer_id` is labeled `"Walk-in / Unknown"` (`WALK_IN_LABEL`), never filled with a mode/guessed value; a new `flag_invalid_dates()` catches unparseable AND syntactically-valid-but-implausible dates (future-dated, or >1 year outside the column's own 1st–99th percentile range) — reused by both the quality report and time-based analysis; outlier detection for money/quantity columns is grouped by a category column (e.g. per-branch/per-category) instead of global, so normal variation between groups isn't flagged.
- `customer_analytics_service.py` — aggregates a transaction log into one row per identified customer (RFM: recency/frequency/monetary + favorite branch/category + loyalty usage), excluding walk-in/unidentified rows, then assigns a standard RFM segment (Champions/Loyal/New/At Risk/Lost/Needs Attention) via quantile scoring.
- `return_prediction_service.py` — predicts whether each current customer will purchase again within 90 days using a strictly time-based methodology: trained on a past cutoff (features from history up to it, labels from real outcomes after it — verifiable), then applied forward from the dataset's own most recent date (features from all history, predicting an unknowable future) — no future leakage into training by construction.
- `forecast_service.py` — Holt's linear trend (double exponential smoothing), hand-rolled on pandas/numpy (no new dependency), with a small alpha/beta grid search, an uncertainty band from historical one-step residuals, and a naive-baseline comparison.
- `transaction_analysis_service.py` — orchestrates the above into a plan (shown to the user for confirmation before running, each analysis independently marked viable/not) and executes it.
- `auto_analyze_service.py` — a new `data_understanding` step runs before cleaning; a transaction-log dataset branches into a new `AWAITING_PLAN_CONFIRMATION` job status instead of the generic target-detection flow; `POST /api/auto-analyze/{id}/confirm-plan` resumes it.
- `report_context_service.py` / `report.html` — new Revenue Analytics / Customer RFM / Return Prediction / Sales Forecast report sections, mutually exclusive with the model/clusters sections (a job runs exactly one of the three paths).

Verified end-to-end against the real `coffee_shop_transactions.csv` (69,830 rows) + real answer-key files (used only in tests): see the in-conversation final report for exact numbers (predicted vs. actual returners, forecast vs. actual revenue).

## 0.1 Four "previous report review" fixes applied this task

1. **AI badge** (`report.html`) — the "AI-generated" badge on the AI Business Insights section heading was unconditional, showing even when AI insights had failed. Now gated on `ai_insights.available`.
2. **Business question** (`report_context_service.py`, `report.html`) — the report only stated a business question when the user had filled in a project description (usually blank), otherwise showing nothing. Now always states one, inferred from what was actually analyzed (model target / clusters / transaction analysis / dataset) when no description was given.
3. **Recommendations** (`report_service.py`, `report.html`) — "Top recommendations" only ever pulled from `future_outlook` (which requires a trained model), so any no-model report (dataset-only, clustering, general analysis, transaction-log) always showed "No verified model-driven recommendations are available" even when the AI narrative's own `## Recommendations` section had real content. Now falls back to it (`_extract_ai_recommendations`), clearly labeled with an "AI" badge per bullet.
4. **Negative values** (`cleaning_service.py`) — see §0 above; this was the same underlying bug the coffee-shop spec's refund-handling requirement describes, confirmed and fixed together.

## 1. Stack

- **Backend**: FastAPI, SQLAlchemy + Alembic (PostgreSQL), scikit-learn, XGBoost, pandas, Gemini REST API (google generativelanguage v1beta), ReportLab/HTML for reports.
- **Frontend**: Next.js (App Router), React, TypeScript, Tailwind, React Query (`@tanstack/react-query` v5), Recharts (or similar) for charts.
- **Tests**: pytest (backend, 25 files, 204+ tests, in-memory SQLite), `tsc --noEmit` for frontend type-checking.

## 2. Data flow

```
Upload (datasets.py)
  → dataset_service.build_dataset_profile()      [Phase 2: column intelligence]
  → cleaning_service.analyze_quality()/clean_dataset()  [Phase 3: quality + versioned cleaning]
  → eda_service.run_eda() / run_filtered_eda()    [Phase 6: adaptive EDA]
  → auto_analyze_service (orchestrator)
      → dataset_service.score_target_candidates() [Phase 4: explainable target scoring]
      → ml_service.train_models()                 [Phases 5,7,8: leakage guard, registry, training]
      → prediction_service                        [Phase 9: single/batch/contributions]
      → ai_service → gemini_service                [Phase 10/11: insights + chat]
      → report_service/report_context_service      [Phase 12: adaptive PDF/HTML report]
```

## 3. Backend services (`backend/app/services/`)

| File | Lines | Responsibility |
|---|---|---|
| dataset_service.py | 372 | Column profiling, semantic typing, ID detection, target-candidate scoring |
| cleaning_service.py | 414 | Quality detection + versioned, non-destructive cleaning |
| eda_service.py | 513 | Adaptive EDA (`run_eda`) + BI-style filtered dashboard (`run_filtered_eda`) |
| ml_service.py | 885 | Model registry, preprocessing, training, leakage-aware feature selection, metrics |
| prediction_service.py | 182 | Single/batch prediction, feature contributions |
| gemini_service.py | ~310 | Gemini REST client — caching, structured logging, error classification |
| ai_service.py | ~130 | Provider orchestration (Gemini-first, Ollama fallback for Insights only) |
| context_service.py | 356 | Builds verified structured context for Gemini (never raw dataset) |
| auto_analyze_service.py | ~450 | End-to-end orchestrator + background job state machine |
| report_context_service.py | 562 | Adapts report content by task type, humanizes names |
| report_service.py | 205 | Renders final PDF/HTML, has a raw-dict-dump safety net |

## 4. Phase-by-phase gap audit (vs. the "Any Dataset → Complete Data Scientist" spec)

| Phase | Status | Evidence |
|---|---|---|
| 1. Audit | ✅ Done (this doc) | |
| 2. Dataset Intelligence | ✅ Present, strong | `build_dataset_profile()` — rows/cols/duplicates/missing/unique/stats per column; `detect_semantic_type()` — numeric/boolean/datetime/categorical/text; `is_id_like_column()`. Minor gap: no explicit "uniqueness ratio" or "constant"/"high-cardinality" semantic labels (only indirectly via `is_id_like`). |
| 3. Data Quality | ✅ Present, strong | `analyze_quality()` — duplicates, missing, impossible values, IQR outliers, invalid dates, mixed types; unit-stripping and category-case normalization as real cleaning transforms; original file never mutated (`_cleaned_v{n}.csv` versioning); before/after tracked in `cleaning_log_json`. |
| 4. Target + Problem Detection | ⚠️ **Partial — real gap** | `score_target_candidates()` is genuinely explainable (name-hint vocabulary, cardinality signal, description bonus, human-readable `reason` per candidate). **But**: only classification/regression are ever detected — no clustering/time-series/anomaly path — and when no candidate is found, Auto Analyze **raises and fails the job** instead of "allow analysis without supervised ML" as the spec requires. **→ Fixed in this task (see §5).** |
| 5. Leakage Engine | ✅ Present, strong, tested | `leakage_warnings`, `post_outcome_excluded`/`post_outcome_included` with reason/severity, re-inclusion supported via Modeling tab retrain. Covered by `test_target_and_leakage.py`. |
| 6. Intelligent EDA | ✅ Present, adaptive | Dtype/cardinality-aware chart selection in both EDA engines. Gap: no ROC/PR curve chart anywhere (confusion matrix and actual-vs-predicted exist, in report charts). |
| 7. Model Registry | ⚠️ **Partial — real gap** | Classification: Logistic Regression, Decision Tree, Random Forest, Gradient Boosting, XGBoost, KNN. Regression: Ridge (used for both "linear" and "ridge"), Random Forest, Gradient Boosting, XGBoost. **Missing**: Extra Trees (both tasks), SVM, Lasso, a real Decision Tree Regressor. **Clustering/time-series: absent entirely.** **→ Registry expanded in this task (see §5); clustering/time-series scoped out, see §6.** |
| 8. Automatic Training | ⚠️ **Partial — real gap** | Preprocessing (median/mode imputation, scaling, OHE), stratified split, 5-fold CV, full metric set (incl. MAPE) all present and solid. **Missing**: class-imbalance handling (`class_weight`, resampling) — confirmed zero hits for `class_weight|SMOTE|imbalance` besides one code comment. **→ Fixed in this task.** |
| 9. Prediction Engine | ✅ Present, strong | Single + batch (capped 5000 rows), auto-generated form (`_build_feature_schema` — dropdowns/medians), feature contributions (exact for linear, honest `"global_importance"` label for trees), "Load from Dataset" in `PredictionsTab.tsx`. |
| 10/11. Gemini + Chat | ✅ Present, hardened | Fully reworked in the immediately preceding task in this session: TTL cache + `force`, structured/sanitized logging, 401/403/404/429/timeout/network classification, Ollama fallback for Insights (never Chat), DB-persisted insight cache keyed by dataset `cleaning_version` + model, unified per-reason messaging across Chat/Insights/Auto Analyze, httpx key-leak fix. |
| 12. Report Engine | ⚠️ **Partial** | Adapts regression vs. classification sections, humanizes all feature names, has an explicit anti-raw-dump safety net (`_assert_no_raw_python_dumps`). **Missing**: no clustering/time-series section (because no such analysis exists yet). |
| 13. Auto Analyze | ⚠️ **Partial — real gap** | Real progress stepper, honest step status (`warning` not silently `completed`), background jobs via FastAPI `BackgroundTasks`, target-confirmation UI. **Gap**: hard-fails with no fallback when no target is found. **→ Fixed in this task.** |
| 14. Professional UX | ✅ Mostly present | Auto Analyze, Regenerate, Retry, suggested AI questions, Export Report all exist. Not deeply re-audited this pass (lower priority than functional gaps). |
| 15/16. Testing/QA | ✅ Ongoing | 204 backend tests passing baseline; new tests added per fix (see final report). |

## 5. What this task actually changed (verified — see final in-conversation report for full detail)

- **Model registry** (`ml_service.py`): added Extra Trees (classifier + regressor), SVM (SVC/SVR), Lasso Regression, and a real Decision Tree Regressor. `recommend_model_keys()` now gates SVM/KNN out of the *automatic* (Auto Analyze) path past 5,000/20,000 rows respectively — manual selection via the Modeling tab is never restricted.
- **Class imbalance** (`ml_service.py`): `_classification_class_weight()` detects a ≥3:1 majority:minority ratio and applies `class_weight="balanced"` to every classifier that supports it; recorded in `preprocessing_json.class_weight_applied` for transparency.
- **General Analysis (no-target) path** (`auto_analyze_service.py`, `ml_service.run_clustering_analysis`, `report_context_service.py`, `report.html`): when `score_target_candidates()` finds nothing, Auto Analyze no longer fails the job. It now runs K-Means clustering (k chosen by silhouette score, 2–6 range) on numeric/non-ID-like columns when ≥2 such columns and ≥20 rows exist; otherwise falls through to an EDA-only summary. Either way the job completes, AI insights and a real PDF/HTML report are generated (report gained a new "Cluster Analysis" section with a size chart and per-cluster profiles), and the frontend (`AutoAnalyzePanel.tsx`) renders the real outcome instead of assuming a target always exists.
- Verified end-to-end via 7 new tests in `test_general_analysis.py` (real dataset upload → real background job → real KMeans → real PDF generation → HTML preview assertions), plus unit tests for `recommend_model_keys` and `_classification_class_weight`. Full backend suite re-run clean after the change: 211/211 passing (204 pre-existing + 7 new).

## 6. Explicit known limitation

**Time-series forecasting is not implemented.** No forecasting library (statsmodels/Prophet) is currently a dependency, and building a correct, tested forecasting pipeline (date-index validation, seasonality detection, train/holdout evaluation, forecast charts, a new report section) is a substantial standalone feature — attempting it inside this same pass risked shipping something unverified, which conflicts with this project's standing rule of never claiming untested functionality complete. This is flagged here rather than silently skipped.
