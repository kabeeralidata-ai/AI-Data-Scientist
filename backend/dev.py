"""Canonical way to start the backend in development. Always runs with --reload, so
code changes are picked up automatically — a long-lived process started WITHOUT
--reload silently keeps running stale code after an edit (a real bug found during this
project's Phase 6 verification: a stale process didn't have a newly-registered Jinja
filter, so every report crashed until the process was restarted). Run with:

    python dev.py
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
