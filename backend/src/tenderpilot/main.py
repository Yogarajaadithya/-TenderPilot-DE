"""TenderPilot DE FastAPI application - foundation chapter.

Liveness and readiness. Readiness performs two real, bounded checks
through restricted runtime credentials: a database query (Chapter 5
Part A, see db.py) and an object storage HeadBucket (Chapter 5's
remaining "connect the API to storage" item, see storage.py). The
queue check doesn't exist yet - nothing in this backend talks to
RabbitMQ, so config.get_dependency_status() still reports it as
"not_configured" rather than pretending otherwise.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from tenderpilot.config import get_dependency_status, get_settings
from tenderpilot.db import dispose_engine, get_database_status
from tenderpilot.logging_config import configure_logging
from tenderpilot.middleware import RequestIdMiddleware
from tenderpilot.storage import dispose_client, get_storage_status

# Fail loudly at import time if config is invalid - never start the app on
# a broken config. This is intentionally not wrapped in try/except.
settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("tenderpilot")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    # Release any pooled database connections and the storage client
    # cleanly on shutdown, rather than leaving them to be dropped when
    # the process exits.
    await dispose_engine()
    await dispose_client()


app = FastAPI(
    title="TenderPilot DE API",
    version="0.1.0",
    description="Foundation chapter - liveness/readiness, now backed by a real database check.",
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)


class LivenessResponse(BaseModel):
    status: Literal["alive"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    app_env: str
    dependencies: dict[str, str]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


@app.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Is the process alive and able to respond at all? No dependency
    checks - this endpoint answering means the ASGI server and this
    process are up, nothing more."""
    return LivenessResponse(status="alive")


@app.get("/health/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    """Can this app actually do useful work right now?

    Database and object storage are the two dependencies this chapter
    actually checks live - a bounded, real "SELECT 1" through the
    restricted tenderpilot_app role (see db.py), and a bounded, real,
    read-only HeadBucket through that same restricted identity against
    SeaweedFS (see storage.py). Everything else in dependencies comes
    from config.get_dependency_status(), which never does I/O - a
    "credentials_present" there is never conflated with "verified
    working" here.

    Timeout ordering (see BOOK.md's chapter on this): both checks are
    capped at 2s (db.DATABASE_HEALTH_CHECK_TIMEOUT_SECONDS and
    storage.STORAGE_HEALTH_CHECK_TIMEOUT_SECONDS), which stays
    comfortably under this route's own callers' timeouts
    (apps/web/app/api/health/route.ts's 5s fetch, and the frontend
    poller's 6s check timeout) - each outer layer must be able to wait
    long enough for the layer it calls to finish on its own, or it ends
    up reporting a generic "timed out" instead of the real, specific
    answer the inner layer would have given.
    """
    dependencies = get_dependency_status(settings)
    dependencies["database"] = await get_database_status(settings)
    dependencies["object_storage"] = await get_storage_status(settings)

    all_healthy = (
        dependencies["database"] == "healthy" and dependencies["object_storage"] == "healthy"
    )
    response.status_code = (
        status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return ReadinessResponse(
        status="ready" if all_healthy else "degraded",
        app_env=settings.app_env.value,
        dependencies=dependencies,
    )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # FastAPI's default validation error body can include submitted field
    # values verbatim. This app never has secret-bearing request bodies at
    # this chapter (no forms take credentials), but the handler is written
    # to never echo raw exc.errors() regardless, so that stays true as
    # endpoints are added later.
    logger.warning(
        "validation_error",
        extra={"request_id": _request_id(request), "path": str(request.url.path)},
    )
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error=ErrorDetail(
                code="validation_error",
                message="Request did not match the expected shape.",
                request_id=_request_id(request),
            )
        ).model_dump(),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code="http_error",
                message=str(exc.detail),
                request_id=_request_id(request),
            )
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never include str(exc) in the response - an unexpected exception could
    # in principle carry sensitive data (a raised config/DB error, say) into
    # its message. Log the real exception server-side only; the client gets
    # a generic message plus the request_id to correlate against logs.
    logger.exception(
        "unhandled_exception",
        extra={"request_id": _request_id(request), "path": str(request.url.path)},
    )
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(
                code="internal_error",
                message="An unexpected error occurred.",
                request_id=_request_id(request),
            )
        ).model_dump(),
    )
