"""Request ID middleware.

Every request gets a UUID, visible in the response header (X-Request-Id)
and attached to every log line emitted while handling that request. This is
what lets "which request caused this error" be answerable later, once real
logs exist to search through.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("tenderpilot.request")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        logger.info(
            "request_started",
            extra={"request_id": request_id},
        )

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id

        logger.info(
            "request_finished",
            extra={"request_id": request_id},
        )
        return response
