from contextlib import asynccontextmanager

import structlog
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.middleware import RequestMiddleware
from app.api.routes import router
from app.core.config import get_settings
from app.core.jobs import JobStore
from app.core.session_store import SessionStore

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
settings = get_settings()


def security_headers() -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if settings.environment != "development":
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self'"
        )
    return headers


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        if app.state.session_store.redis is not None:
            await app.state.session_store.redis.aclose()
        await app.state.jobs_redis.aclose()
        middleware = app.middleware_stack
        while middleware is not None:
            if isinstance(middleware, RequestMiddleware):
                await middleware.close()
                break
            middleware = getattr(middleware, "app", None)


app = FastAPI(
    title=settings.project_name,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.yaml",
    lifespan=lifespan,
)
# Security response headers
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for name, value in security_headers().items():
            response.headers[name] = value
        return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins or [],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-Correlation-ID"],
    expose_headers=["X-Correlation-ID"],
)
app.add_middleware(RequestMiddleware, settings=settings)
app.add_middleware(SecurityHeadersMiddleware)
session_redis = (
    Redis.from_url(settings.redis_url, decode_responses=True)
    if settings.idempotency_backend == "redis"
    else None
)
app.state.session_store = SessionStore(session_redis)
app.state.jobs_redis = Redis.from_url(settings.redis_url, decode_responses=True)
app.state.job_store = JobStore(app.state.jobs_redis, settings.job_processing_ttl_seconds, settings.job_retention_seconds, settings.idempotency_ttl_seconds)
frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="frontend-assets")


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        error = detail["error"]
        error.setdefault("details", {})
    else:
        error = {
            "code": "HTTP_ERROR",
            "message": str(detail),
            "retryable": exc.status_code >= 500,
            "details": {},
        }
    return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "REQUEST_VALIDATION_FAILED",
                "message": "The request did not match the API schema.",
                "retryable": False,
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all: never expose internal details to the caller."""
    structlog.get_logger(__name__).error(
        "unhandled_exception",
        error_type=type(exc).__name__,
        path=request.url.path,
    )
    headers = security_headers()
    origin = request.headers.get("origin")
    if origin in (settings.cors_allowed_origins or []):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred.",
                "retryable": True,
                "details": {},
            }
        },
        headers=headers,
    )


app.include_router(router)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(frontend_dir / "index.html")
