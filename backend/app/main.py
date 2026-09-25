import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import ai, analysis, auth, auto_analyze, datasets, models, predictions, projects, reports
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# httpx (used by gemini_service for every Gemini REST call) logs each outgoing request at
# INFO level, INCLUDING THE FULL URL — and Gemini's REST API takes the API key as a query
# parameter (?key=...), so httpx's default logging silently wrote the raw Gemini API key to
# the server log on every request. gemini_service's own logging never includes the key; this
# only suppresses httpx/httpcore's own built-in request/connection logging, which is not
# otherwise needed. See the security audit that found this: the key appeared verbatim in
# backend logs despite gemini_service.py taking care never to log it directly.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(title=settings.APP_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "Invalid request data.", "errors": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
    )


app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(datasets.router)
app.include_router(analysis.router)
app.include_router(models.router)
app.include_router(predictions.router)
app.include_router(ai.router)
app.include_router(reports.router)
app.include_router(auto_analyze.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": settings.APP_NAME}
