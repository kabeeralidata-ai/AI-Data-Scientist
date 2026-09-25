# Architecture

## Request flow

```
Browser (Next.js) --REST/JSON--> FastAPI --> PostgreSQL (metadata)
                                     |
                                     +--> Local filesystem (storage/uploads, storage/models, storage/reports)
                                     |
                                     +--> Ollama HTTP API (localhost:11434 or ollama:11434 in Docker)
```

## Layering (backend)

- `app/api/*` — FastAPI routers. Thin: parse request, check ownership, call a service, map
  domain errors to HTTP responses. No business logic here.
- `app/services/*` — all business logic (dataset profiling, cleaning, EDA, ML training,
  predictions, Ollama calls, report generation). Pure Python + pandas/sklearn, independent
  of FastAPI.
- `app/models/*` — SQLAlchemy ORM models (one file per table).
- `app/schemas/*` — Pydantic request/response contracts.
- `app/core/*` — config (env vars), security (JWT/password hashing), database session,
  and `GUID`, a database-agnostic UUID column type so the same models run against both
  PostgreSQL (production/Docker) and SQLite (fast in-memory tests).
- `app/utils/*` — stateless helpers (file validation, metrics calculation).

## Why a `GUID` type instead of PostgreSQL's native UUID?

The test suite runs against an in-memory SQLite database for speed and to avoid requiring
a live Postgres for CI. PostgreSQL's native `UUID` SQLAlchemy type does not work against
SQLite. `app/core/types.py` defines a `TypeDecorator` that stores real UUIDs in Postgres
and a 32-char hex string in SQLite, transparently, so every model works unmodified against
either backend.

## The AI boundary

`app/services/ollama_service.py` is the only module that talks to Ollama. Every caller
(`app/api/ai.py`, `app/services/report_service.py`) passes it a small, pre-computed
**context dict** built by `app/services/context_service.py` from already-persisted,
deterministic results (dataset profile, EDA summary, model metrics, feature importance).
Ollama is asked only to *explain* that context in natural language — the system prompt
explicitly forbids inventing or recalculating numbers. If Ollama is unreachable or times
out, `OllamaUnavailableError` is raised and every caller falls back to a clear, honest
message instead of failing the request — the rest of the platform (upload, cleaning, EDA,
training, predictions, reports) keeps working.

## Data isolation

Every dataset/model/report is scoped to a `project`, and every project is scoped to a
`user`. All API handlers that accept a resource id look it up and then verify the owning
project's `user_id` matches the authenticated user before returning anything — see
`get_owned_project` / `get_owned_dataset` / `get_owned_model` / `get_owned_report` in
`app/api/*.py`. A user requesting another user's resource gets a 404, not a 403, to avoid
confirming the resource's existence.

## Frontend structure

- `app/*` — Next.js App Router pages. Route-level components only (data fetching via
  hooks, composition of feature components).
- `components/ui/*` — presentational, reusable primitives (Button, Input, Card, Modal,
  Drawer, Tabs, Table, Badge, Alert, toast/loading/empty/error states).
- `components/layout/*` — Sidebar, Navbar, mobile Drawer, the `AppShell` that guards
  authenticated routes client-side and assembles the responsive shell.
- `components/charts/*` — Recharts-based, theme-aware, responsive chart wrappers.
- `components/projects/*` — one component per project-workspace tab (Dataset, Cleaning,
  EDA, Modeling, Predictions, AI, Reports); these own their tab's data fetching via the
  hooks in `hooks/*` and are composed together in `app/projects/[id]/page.tsx`.
- `hooks/*` — one file per resource, each wrapping TanStack Query for server state.
- `lib/*` — axios client with auth header injection + 401 handling, auth context (JWT in
  localStorage, not cookies — see below), toast context, generic utils.

### Why localStorage + Authorization header instead of cookies?

The backend is a stateless JWT API (`python-jose`), matching the spec's "JWT
authentication" requirement directly, and the frontend calls it as a separate origin
(`NEXT_PUBLIC_API_URL`). Route protection is therefore enforced client-side in
`AppShell` (redirects to `/login` if there is no authenticated user) rather than via
Next.js middleware reading a cookie. This keeps the backend simple to test and use from
any client (Swagger UI, curl, a future mobile app) without CSRF/cookie-domain concerns.
