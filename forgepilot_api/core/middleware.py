from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from forgepilot_api.core.context import reset_request_id, set_request_id
from forgepilot_api.core.logging import get_logger
from forgepilot_api.core.metrics import get_metrics_registry
from forgepilot_api.core.telemetry import start_span

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, request_id_header: str = "x-request-id"):
        super().__init__(app)
        self.request_id_header = request_id_header.lower()
        self.metrics = get_metrics_registry()

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(self.request_id_header)
        request_id = (incoming or "").strip() or uuid.uuid4().hex
        token = set_request_id(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        with start_span(
            "http.request",
            {
                "http.method": request.method,
                "http.route": request.url.path,
                "http.request_id": request_id,
            },
        ) as span:
            try:
                response: Response = await call_next(request)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.metrics.record_request(request.method, request.url.path, 500, elapsed_ms)
                logger.exception("Unhandled request error method=%s path=%s", request.method, request.url.path)
                if span is not None:
                    span.set_attribute("http.status_code", 500)
                reset_request_id(token)
                raise

            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers[self.request_id_header] = request_id
            self.metrics.record_request(request.method, request.url.path, response.status_code, elapsed_ms)
            if span is not None:
                span.set_attribute("http.status_code", int(response.status_code))
                span.set_attribute("http.latency_ms", float(round(elapsed_ms, 3)))

            if request.url.path.startswith("/health") or request.url.path == "/metrics":
                logger.debug(
                    "request method=%s path=%s status=%s latency_ms=%.2f",
                    request.method,
                    request.url.path,
                    response.status_code,
                    elapsed_ms,
                )
            else:
                logger.info(
                    "request method=%s path=%s status=%s latency_ms=%.2f",
                    request.method,
                    request.url.path,
                    response.status_code,
                    elapsed_ms,
                )
            reset_request_id(token)
            return response
