# AI-Powered Data Scientist & Business Analytics Platform

A full-stack analytics platform that lets users upload business datasets, automatically
clean and explore them, train and compare machine-learning models, generate predictions,
and receive AI-powered business explanations via a local Ollama + Qwen model.

> **Core principle:** Python/scikit-learn calculates every metric and prediction.
> Qwen (via Ollama) only *explains* those verified numbers in plain business language —
> it never invents or recalculates them.

---

## Features

- Email/password authentication with JWT access tokens
- Project management (create, update, archive analytics projects)
- CSV / XLSX / XLS dataset upload with validation and automatic profiling, plus a
  one-click **"Try with sample dataset"** for new users
- **Auto Analyze**: one click runs Profile → Recommended Cleaning → EDA → Target
  Detection → Train Suitable Models → Compare → AI Insights → Report as a background
  job, with a live progress stepper. Target detection is a **scored heuristic** (name
  hints, cardinality, description mentions — never a grouping/descriptive column like
  "store_city" outranking a real outcome like "sales_amount"), and the pipeline **pauses**
  to show the top 3 candidates with reasons for the user to confirm or override before
  training starts; the confirmed choice is remembered for future runs on that project
- Data cleaning with **recommended, checkbox-driven fixes** (auto strategy per column)
  or manual single-strategy control; always re-derives from the original upload, so
  cleaning is reproducible and versioned, and the original file is never modified
- Automated EDA: descriptive stats, distributions, outliers, correlations, category
  frequency, time trends, with an **"Explain this"** AI button on key findings
- Interactive charts (bar, line, histogram, scatter, correlation heatmap) built with
  Recharts, responsive and theme-aware
- **Smart Training**: auto-excludes ID-like columns; raw date columns aren't just
  dropped — year/month/day-of-week are extracted as real features. Every training run
  also runs **data-leakage detection** at two levels: single-column (a feature ≥95%
  correlated with the target — e.g. a "profit" column that's a fixed fraction of
  "sales_amount") and **pairwise/derived** (a PAIR of features whose simple comparison
  almost exactly reproduces a binary target — e.g. "late_delivery" literally defined as
  "delivery_time_min > promised_time_min", which no single-column correlation check can
  see). Both are excluded by default with a visible warning explaining why. A separate,
  softer check flags features that are only *named* like post-outcome information (a
  "tip" or "rating" logged after the fact) for the user to review — these are **not**
  auto-excluded, since a name alone isn't proof, and wrongly dropping a genuinely
  available feature would be its own mistake. Training also compares the best model
  against a **majority-class/mean-predictor baseline**; if the model doesn't clearly beat that
  baseline (judged against the model's own cross-validation variance, not a fixed
  threshold), a "barely beats baseline" warning is shown instead of a false sense of
  confidence. Test size / CV folds / model selection are tucked behind "Advanced
  Settings" for power users
- ML training: Logistic/Linear Regression, Decision Tree, Random Forest, Gradient
  Boosting, XGBoost, KNN — with automatic classification/regression detection
- Model comparison table with cross-validated metrics, feature importance, and the
  baseline comparison above; an AI insight is generated automatically once training
  finishes
- **Auto-generated prediction form**: dropdowns for categorical features (populated from
  real training-time values), numeric fields prefilled with the median/mode, ID and date
  columns hidden, a **"Load from Dataset"** picker to fill the form from a real row, and
  real per-prediction feature contributions (exact for linear models, global importance
  as an honest fallback for tree ensembles)
- **Batch prediction**: upload a CSV, get predictions for every row, download the results
- AI Insights and AI Chat over verified structured analysis context — with suggested
  question chips generated from the project's real columns/target — and graceful
  degradation when Ollama is unreachable. The chat is **grounded**: if a question
  mentions a concept with no matching column (e.g. asking about "churn" in a
  retail-sales project), the backend detects this and answers honestly with the
  project's real available columns instead of letting the LLM guess
- **Honest pipeline status**: Auto Analyze steps show green (succeeded), yellow
  (completed with an issue, e.g. AI unavailable, or a weak-model warning), or red
  (failed) — never a blanket green checkmark for a step that didn't fully succeed. A
  **Retry** button re-runs just the AI insights step once Ollama is reachable again,
  without re-running training. The same success/warning status propagates to the
  per-tab checkmarks in the project workspace
- **Two AI providers**: Ollama/Qwen (local, default) or **Gemini** (Google AI Studio's
  free tier, `AI_PROVIDER=gemini`) — Ollama always stays available as an automatic
  fallback when Gemini is primary. A Settings page card (and a navbar status dot) shows
  the active provider's reachability, the exact reason if not (invalid key, rate
  limited, server unreachable, model not installed, timeout), and a "Test connection"
  button
- PDF report generation (dataset overview, EDA, modeling, AI insight, limitations)
- **Guided workflow**: per-tab completion checkmarks (success/warning-aware), "Next
  Step →" navigation, and a "Continue where you left off" card on the dashboard that
  remembers your place per project (browser-local)
- 100% responsive UI (mobile drawer nav, scrollable tables, resizing charts)
- Docker Compose stack: frontend, backend, PostgreSQL, Ollama

---

## Architecture

```
Next.js (React/TS) ──REST──> FastAPI ──> PostgreSQL (metadata)
                                │
                                ├─> Pandas / scikit-learn / XGBoost (ML engine)
                                │      └─> joblib model artifacts on disk
                                └─> Ollama (Qwen) for natural-language explanations
```

Backend folder structure follows `app/api`, `app/core`, `app/models`, `app/schemas`,
`app/services`, `app/utils` — see `backend/`.

Frontend folder structure follows `app/`, `components/`, `hooks/`, `lib/`, `types/` —
see `frontend/`.

---

## Tech Stack

| Layer          | Technology                                                   |
|----------------|---------------------------------------------------------------|
| Frontend       | Next.js, React, TypeScript, Tailwind CSS, TanStack Query, Recharts |
| Backend        | FastAPI, Pydantic, SQLAlchemy, Alembic, JWT (python-jose)     |
| Data Science   | Pandas, NumPy, SciPy, scikit-learn, XGBoost, joblib            |
| AI             | Ollama + Qwen                                                  |
| Database       | PostgreSQL                                                      |
| Reports        | Jinja2 + xhtml2pdf                                             |
| Dev/Deploy     | Docker, Docker Compose, pytest, ESLint                          |

---

## Getting Started (Docker — recommended)

1. Copy the environment template:

   ```bash
   cp .env.example .env
   ```

2. Start the full stack:

   ```bash
   docker compose up --build
   ```

3. Pull the Qwen model into the Ollama container (first run only):

   ```bash
   docker compose exec ollama ollama pull qwen2.5:3b
   ```

4. Open the app:
   - Frontend: http://localhost:3000
   - Backend API docs: http://localhost:8000/docs

The analytics platform works fully **without** Ollama — AI Insights/Chat will show
"AI service is currently unavailable" while every other feature (upload, cleaning,
EDA, charts, ML training, predictions, reports) keeps working.

---

## Getting Started (Local development, no Docker)

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
cp ../.env.example .env        # edit DATABASE_URL to point at your local Postgres

alembic upgrade head
uvicorn app.main:app --reload
```

Backend runs at http://localhost:8000 (interactive docs at `/docs`).

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL if needed
npm run dev
```

Frontend runs at http://localhost:3000.

### Ollama

Install Ollama locally (https://ollama.com), then:

```bash
ollama pull qwen2.5:3b   # must match OLLAMA_MODEL exactly — see below
ollama serve
```

Set `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and `OLLAMA_TIMEOUT_SECONDS` in `backend/.env` to
match your setup. Getting this wrong is the most common cause of "AI service
unavailable" — the three things to check, in order:

1. **`OLLAMA_BASE_URL` must match where the backend can actually reach Ollama, not where
   you can reach it from your browser:**
   - Backend running directly on your machine (no Docker), Ollama also local:
     `http://localhost:11434` (the default).
   - Backend running in Docker Compose, Ollama **also in Compose** (the bundled
     `ollama` service in `docker-compose.yml`): `http://ollama:11434` — Compose's
     internal DNS resolves the service name, `localhost` inside the backend container
     means the container itself, not your host.
   - Backend running in Docker, Ollama running on the **Windows/Mac host** instead of in
     Compose: `http://host.docker.internal:11434`.
2. **`OLLAMA_MODEL` must exactly match an installed tag** from `ollama list` on the
   Ollama host — e.g. `qwen2.5:3b`, not just `qwen` or `qwen2.5`. A mismatched tag
   connects successfully but fails every generation request.
3. **`OLLAMA_TIMEOUT_SECONDS`** (default 120) — increase this if you're on a slower
   machine and see timeouts on longer AI Insight generations.

To diagnose which of these is wrong without reading logs: open **Settings → AI Status**
in the app (or `GET /api/ai/status`) and click **Test connection**. It reports the exact
reason — server unreachable, model not installed (with the list of what *is* installed),
or timed out — plus the URL and model it's actually configured with. A small status dot
in the navbar also reflects this at a glance.

Whether or not Ollama is reachable, all deterministic analysis (profiling, cleaning,
EDA, training, predictions, reports) works fully — only the AI-generated narrative and
chat degrade gracefully.

### Gemini (optional — free tier, no local server required)

If you'd rather not run a local Ollama server, AI Insights and AI Chat can use Google's
Gemini API instead:

1. Get a free API key from [Google AI Studio](https://aistudio.google.com/apikey).
2. In `backend/.env`, set:
   ```bash
   AI_PROVIDER=gemini
   GEMINI_API_KEY=your-key-here
   GEMINI_MODEL=gemini-3.6-flash   # or another free-tier Flash model — check AI Studio for current names,
                                    # since Google periodically retires older model tags for new API keys
   GEMINI_TIMEOUT_SECONDS=120   # newer "thinking" Flash models can take a while — increase further if you see timeouts
   ```
3. Restart the backend. **Settings → AI Status** now shows Gemini as the primary
   provider, plus Ollama's status underneath as the automatic **fallback** — if Gemini
   is unreachable, rate-limited, or misconfigured, every AI Insight/Chat request
   transparently retries against Ollama (if it's running) before degrading to the
   honest "AI unavailable" message. Nothing else in the app changes: only the two AI
   endpoints (`/api/ai/insights`, `/api/ai/chat`) and Auto Analyze's AI step are
   affected, and only structured, pre-calculated summaries (never raw dataset rows) are
   ever sent to Gemini — same as Ollama.

Error handling specifics: an invalid/expired API key fails immediately with a clear
`invalid_key` reason (no retry — retrying won't fix a bad key); a `429` rate-limit
response is retried automatically with exponential backoff (up to 3 attempts) before
falling back to Ollama; a network-level failure reports `unreachable`. The API key is
never written to logs — only the failure reason and HTTP status are logged, never the
key or the full request URL.

---

## Using the Guided Workflow

For a first-time user, the fastest path through the platform is:

1. **Dashboard → New project**, then in the Dataset tab either upload your own CSV/XLSX
   or click **"Try with sample dataset"** (a bundled, realistic customer-churn dataset —
   no file of your own required).
2. Click **Run Auto Analyze**. This runs Profile → Recommended Cleaning → EDA → Target
   Detection as one background job with a live step tracker, then **pauses**: it shows
   the top 3 target-column candidates with a reason each (e.g. "sales_amount — a core
   business outcome" vs. "store_city — a grouping/descriptive field, not an outcome") for
   you to confirm or pick a different one. Once confirmed (remembered for next time on
   this project), it continues — training every suitable model, excluding any feature
   that's a near-duplicate of the target (data leakage) with a visible warning, comparing
   against a baseline, picking the best model, generating an AI insight, and building a
   PDF report. Each step shows green (succeeded), yellow (completed with an issue — e.g.
   AI unavailable, with a **Retry** button — or a weak-model warning), or red (failed).
   You can keep using other tabs while it runs.
3. Once it finishes, the **Predictions** tab has a form auto-generated from the trained
   model: categorical fields are dropdowns populated with real values seen during
   training, numeric fields are prefilled with the median. Use **"Load from Dataset"**
   to pull in a real row instead of typing values by hand, or switch to **Batch
   Prediction** to score a whole CSV at once and download the results.
4. Every tab has a completion checkmark once you've done real work there, and a
   **"Next Step →"** button at the bottom — or just use the **"Continue where you left
   off"** card on the dashboard next time you come back to a project.

Advanced users aren't locked into this: every individual tab (Data Quality, EDA,
Modeling, Predictions, AI, Reports) still works standalone, and **"Advanced Settings"**
in the Modeling tab exposes test size, cross-validation folds, and which specific models
to train — remembered per browser for next time.

---

## Environment Variables

See [`.env.example`](.env.example) for the full list (database, JWT secret, file
storage paths/limits, Ollama base URL/model/timeout, CORS origins, frontend API URL).
Never commit a real `.env` file.

---

## Running Tests

### Backend

```bash
cd backend
venv\Scripts\activate
pytest -v
```

Tests run against an in-memory SQLite database (via a portable `GUID` column type) so
they require no external services, and include: registration/login, authorization
boundaries between users, file validation, cleaning, EDA, ML training + comparison,
predictions, and AI-service failure handling (Ollama unreachable is treated as a
normal, tested code path — not an unhandled exception).

`tests/test_smart_features.py` and `tests/test_auto_analyze.py` cover: ID-like column
detection and exclusion, target-column suggestion, custom CV folds, the auto-generated
feature schema, real per-prediction contributions (both the exact linear-model path and
the honest tree-model fallback), batch prediction (including rejecting a file with
missing columns), the data-quality endpoint, cleaning reproducibility from the original
upload across repeated cleans, the sample-dataset endpoint, and the full Auto Analyze
pipeline end-to-end (including cross-project/cross-user authorization on it, honest
step status when Ollama is unavailable, the retry-ai-insights endpoint, and the
target-confirmation pause/resume/remember-for-next-run flow).

`tests/test_target_and_leakage.py` and `tests/test_feature_resolution.py` cover the
target-scoring heuristic (a grouping column like `store_city` never outranking a real
outcome like `sales_amount`, even when both are name-hinted), single-column data-leakage
detection and exclusion (a feature ≥95% correlated with the target), pairwise/derived-
leakage detection (a target that's literally defined as one column exceeding another,
e.g. `late_delivery` == `delivery_time_min > promised_time_min` — including a sanity
check that the single-column check alone genuinely can't catch this case), post-outcome
features excluded **by default** with an explicit opt-in override
(`include_post_outcome_features`), the baseline comparison and weak-model warning on
both a learnable and a genuinely unpredictable (random) target, and date-column
extraction into year/month/day-of-week features. `tests/test_cleaning_units.py` covers
unit-stripping ("4.2 km" → `4.2`), categorical text-case normalization, and impossible-
negative-value correction. `tests/test_karachi_end_to_end.py` runs the full Auto Analyze
pipeline against a real, messy dataset end-to-end. `tests/test_ai_service.py` covers the
Ollama connection-diagnostics reasons and AI chat grounding (a question about a concept
with no matching column gets an honest, column-listing answer instead of an LLM guess).
`tests/test_gemini_service.py` covers the Gemini client (invalid key, rate-limit retry
with backoff, timeout, unreachable, blocked/empty responses, and that the API key is
never logged) and the provider-orchestration fallback (Gemini → Ollama) — all via a
monkeypatched `httpx.Client.post`, no real network calls or API key required.

Auto Analyze runs as a `BackgroundTask`, which FastAPI's `TestClient` executes
synchronously before the request returns — so these tests need no polling loop, just
like a real client polling `GET /api/auto-analyze/{job_id}` would in the browser.

### Frontend

```bash
cd frontend
npm run test
```

---

## API Overview

```
POST   /api/auth/register
POST   /api/auth/login
GET    /api/auth/me

GET    /api/projects
POST   /api/projects
GET    /api/projects/{id}
PATCH  /api/projects/{id}
DELETE /api/projects/{id}

POST   /api/projects/{id}/datasets/upload
POST   /api/projects/{id}/datasets/sample       # try-it sample dataset
GET    /api/projects/{id}/datasets
GET    /api/datasets/{id}
DELETE /api/datasets/{id}
GET    /api/datasets/{id}/quality               # computed data-quality issues + recommended fixes
GET    /api/datasets/{id}/rows                   # sample rows, backs "Load from Dataset"

POST   /api/analysis/{dataset_id}/eda
POST   /api/analysis/{dataset_id}/clean          # missing_strategy: auto | mean | median | mode | constant | drop

POST   /api/models/train                         # auto-excludes ID-like columns, extracts date features,
                                                   # excludes single-column (>=0.95 correlated) and pairwise/
                                                   # derived leakage, flags post-outcome-named features for
                                                   # review; every model's metrics_json includes a baseline comparison
GET    /api/models/project/{project_id}
GET    /api/models/{id}                          # includes feature_schema_json for the prediction form

POST   /api/predictions                          # includes real per-prediction feature contributions
POST   /api/predictions/batch                     # CSV upload -> predictions for every row

POST   /api/projects/{id}/auto-analyze            # kicks off the pipeline; pauses after target detection
GET    /api/auto-analyze/{job_id}                 # poll job status + step-by-step progress
GET    /api/projects/{id}/auto-analyze/latest
POST   /api/auto-analyze/{job_id}/confirm-target  # confirms/overrides the target, resumes the pipeline
POST   /api/auto-analyze/{job_id}/retry-ai-insights  # re-attempts just the AI insights step

GET    /api/ai/status                             # active provider + reason; includes a `fallback` block for
                                                   # Ollama's status when AI_PROVIDER=gemini
POST   /api/ai/insights                           # response includes which provider actually generated it
POST   /api/ai/chat                               # grounds questions against the project's real columns
GET    /api/ai/conversations/{project_id}

POST   /api/reports/projects/{project_id}/generate
GET    /api/reports/projects/{project_id}
GET    /api/reports/{id}
GET    /api/reports/{id}/download
```

Full interactive documentation is available at `/docs` (Swagger) once the backend
is running.

---

## Project Structure

```
ai-data-scientist/           (this repo root: D:\AI_Data_Scientist)
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routers
│   │   ├── core/         # config, security, database, cross-DB UUID type
│   │   ├── models/       # SQLAlchemy ORM models
│   │   ├── schemas/      # Pydantic request/response schemas
│   │   ├── services/     # dataset, cleaning, EDA, ML, prediction, Ollama, report,
│   │   │                 # auto_analyze (background pipeline orchestration)
│   │   ├── assets/       # bundled sample dataset for "Try with sample dataset"
│   │   └── utils/        # file, validation, metrics helpers
│   ├── alembic/          # DB migrations
│   ├── tests/            # pytest suite
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── app/               # Next.js App Router pages
│   ├── components/        # layout, ui, charts, datasets, models, ai, reports
│   ├── hooks/, lib/, types/
│   └── Dockerfile
├── storage/                # uploaded files, trained models, generated reports
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Design Principle: Facts vs. Predictions vs. AI Interpretation

The UI always separates these three things, and every AI-generated block is labeled
**"AI-generated insight"** next to the source metrics it explains:

1. **Facts** — calculated deterministically by the analytics engine (e.g. "Revenue
   decreased by 12%").
2. **Model predictions** — produced by the trained ML model (e.g. "Predicted churn
   probability: 82%").
3. **AI interpretation** — generated by Qwen from #1 and #2, never inventing new
   numbers.

---

## What's Implemented vs. Deferred

This build covers the full MVP flow end-to-end (Section 60 of the spec) plus the AI
layer, reports, responsive polish, and the guided/"smart" workflow layer described in
[Using the Guided Workflow](#using-the-guided-workflow) (auto-generated prediction
forms, smart training defaults, one-click Auto Analyze, quality-driven cleaning,
workflow navigation, AI suggested questions, and onboarding/preferences), plus a
correctness layer on top of that: scored (not first-match) target detection with a
user-confirmation pause, data-leakage detection and exclusion, a baseline/weak-model
comparison, honest (success/warning/failed) step status with a retry path, grounded AI
chat, and Ollama connection diagnostics. Auto Analyze runs as a FastAPI `BackgroundTask`
with its own DB session and polling-based progress — it is not backed by a distributed
queue, so it's scoped to single-process background work, not horizontally scaled job
processing.

Per-prediction feature contributions are **real**, not SHAP: exact
`coefficient × scaled value` contributions for linear models, and honest global
feature-importance (clearly labeled as global, not per-instance) as a fallback for
tree ensembles — never a fabricated per-instance number for models that don't support
one.

The following **advanced features** (Section 61 of the spec) are intentionally out of
scope for this pass and are natural next steps: true SHAP explainability for
tree/ensemble models, time-series forecasting, clustering/anomaly detection,
Celery/Redis background job queue (to replace in-process `BackgroundTasks`),
dataset/model diffing UI beyond version numbers, role-based access control, and cloud
object storage. The architecture (service layer, job-style endpoints, versioned models
table) is structured so these can be added without a rewrite.

---

## Future Improvements

- Background job queue (Celery + Redis) to replace in-process `BackgroundTasks` for
  Auto Analyze and large dataset processing/training
- True SHAP-based explainability for tree/ensemble models (current contributions are
  exact for linear models, honest global importance for trees)
- Time-series forecasting and clustering for datasets without a target column
- Anomaly detection
- Scheduled/recurring reports
- Cloud object storage for uploads/models/reports
- Frontend (Vitest) component tests for the new guided-workflow UI (Auto Analyze
  panel, batch prediction, load-from-dataset picker) — currently covered by clean
  `npm run build` type-checks and backend pytest coverage of the underlying APIs, not
  component-level frontend tests
