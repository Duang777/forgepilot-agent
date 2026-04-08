from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from forgepilot_api.core.logging import get_logger
from forgepilot_api.core.rate_limit import RateLimiterUnavailable, build_rate_limiter
from forgepilot_api.core.security import ApiKeyRecord, verify_api_key
from forgepilot_api.storage.repositories import create_audit_log

logger = get_logger(__name__)


def _is_exempt_path(path: str, exempt_paths: list[str]) -> bool:
    normalized = path if path.startswith("/") else f"/{path}"
    for candidate in exempt_paths:
        target = candidate.strip()
        if not target:
            continue
        if not target.startswith("/"):
            target = f"/{target}"
        if target == "/":
            if normalized == "/":
                return True
            continue
        if normalized == target or normalized.startswith(f"{target}/"):
            return True
    return False


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        header_name: str,
        records: list[ApiKeyRecord],
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.header_name = header_name.lower()
        self.records = records
        self.exempt_paths = exempt_paths or []

    async def dispatch(self, request: Request, call_next):
        if _is_exempt_path(request.url.path, self.exempt_paths):
            return await call_next(request)

        candidate = request.headers.get(self.header_name, "")
        record = verify_api_key(candidate, self.records)
        if record is None:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        request.state.auth_subject = record.subject
        request.state.auth_scheme = "api_key"
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        max_requests: int,
        window_seconds: int,
        backend: str = "memory",
        redis_url: str = "redis://127.0.0.1:6379/0",
        redis_key_prefix: str = "forgepilot:ratelimit",
        fail_open: bool = True,
        trust_proxy: bool = False,
        proxy_header: str = "x-forwarded-for",
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1, window_seconds)
        self.trust_proxy = trust_proxy
        self.proxy_header = proxy_header.lower()
        self.exempt_paths = exempt_paths or []
        self.rate_limiter = build_rate_limiter(
            backend=backend,
            redis_url=redis_url,
            redis_key_prefix=redis_key_prefix,
            fail_open=fail_open,
        )

    def _build_identity(self, request: Request) -> str:
        subject = getattr(request.state, "auth_subject", None)
        if subject:
            return f"subject:{subject}"
        if self.trust_proxy:
            forwarded = request.headers.get(self.proxy_header, "").strip()
            if forwarded:
                # x-forwarded-for may contain a chain: client, proxy1, proxy2
                origin = forwarded.split(",")[0].strip()
                if origin:
                    return f"ip:{origin}"
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"

    async def dispatch(self, request: Request, call_next):
        if _is_exempt_path(request.url.path, self.exempt_paths):
            return await call_next(request)

        identity = self._build_identity(request)
        try:
            result = await self.rate_limiter.check(
                identity=identity,
                max_requests=self.max_requests,
                window_seconds=self.window_seconds,
            )
        except RateLimiterUnavailable:
            return JSONResponse({"error": "Rate limiter unavailable"}, status_code=503)
        if not result.allowed:
            retry_after = max(1, int(result.retry_after_seconds or 1))
            return JSONResponse(
                {"error": "Too Many Requests"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)


class AuditMiddleware(BaseHTTPMiddleware):
    MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if request.method.upper() in self.MUTATING_METHODS:
            actor = getattr(request.state, "auth_subject", None) or "anonymous"
            auth_scheme = getattr(request.state, "auth_scheme", None)
            request_id = getattr(request.state, "request_id", None)
            client_ip = request.client.host if request.client else "unknown"
            logger.info(
                "audit actor=%s method=%s path=%s status=%s ip=%s",
                actor,
                request.method.upper(),
                request.url.path,
                response.status_code,
                client_ip,
            )
            try:
                await create_audit_log(
                    request_id=request_id,
                    actor=actor,
                    auth_scheme=auth_scheme,
                    method=request.method.upper(),
                    path=request.url.path,
                    status_code=response.status_code,
                    client_ip=client_ip,
                    metadata={"query": str(request.url.query or "")},
                )
            except Exception:
                logger.exception("failed to persist audit log method=%s path=%s", request.method, request.url.path)
        return response
