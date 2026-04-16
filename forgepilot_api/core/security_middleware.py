from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from forgepilot_api.core.jwt_auth import JwtValidationError, JwtValidationOptions, validate_hs_jwt
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


def _parse_scope_value(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        values = [item.strip().lower() for item in re.split(r"[\s,\|]", raw) if item.strip()]
        return set(values)
    if isinstance(raw, list):
        out: set[str] = set()
        for item in raw:
            if isinstance(item, str):
                token = item.strip().lower()
                if token:
                    out.add(token)
        return out
    return set()


def _subject_scopes(subject: str, subject_scope_map: dict[str, tuple[str, ...]]) -> set[str]:
    key = subject.strip().lower()
    if not key:
        return set()
    values = subject_scope_map.get(key, ())
    return {item.strip().lower() for item in values if item.strip()}


def _set_auth_state(request: Request, *, subject: str, scheme: str, scopes: set[str]) -> None:
    request.state.auth_subject = subject
    request.state.auth_scheme = scheme
    request.state.auth_scopes = sorted(scopes)


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        header_name: str,
        records: list[ApiKeyRecord],
        subject_scope_map: dict[str, tuple[str, ...]] | None = None,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.header_name = header_name.lower()
        self.records = records
        self.subject_scope_map = subject_scope_map or {}
        self.exempt_paths = exempt_paths or []

    async def dispatch(self, request: Request, call_next):
        if _is_exempt_path(request.url.path, self.exempt_paths):
            return await call_next(request)

        candidate = request.headers.get(self.header_name, "")
        record = verify_api_key(candidate, self.records)
        if record is None:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        scopes = _subject_scopes(record.subject, self.subject_scope_map)
        _set_auth_state(request, subject=record.subject, scheme="api_key", scopes=scopes)
        return await call_next(request)


class JwtAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        header_name: str,
        bearer_prefix: str,
        options: JwtValidationOptions,
        subject_claim: str,
        scope_claim: str,
        roles_claim: str,
        subject_scope_map: dict[str, tuple[str, ...]] | None = None,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.header_name = header_name.lower()
        self.bearer_prefix = bearer_prefix.strip().lower()
        self.options = options
        self.subject_claim = subject_claim
        self.scope_claim = scope_claim
        self.roles_claim = roles_claim
        self.subject_scope_map = subject_scope_map or {}
        self.exempt_paths = exempt_paths or []

    def _extract_token(self, request: Request) -> str | None:
        value = str(request.headers.get(self.header_name, "")).strip()
        if not value:
            return None
        prefix = self.bearer_prefix
        if prefix and " " in value:
            lead, rest = value.split(" ", 1)
            if lead.strip().lower() != prefix:
                return None
            token = rest.strip()
            return token or None
        return value

    async def dispatch(self, request: Request, call_next):
        if _is_exempt_path(request.url.path, self.exempt_paths):
            return await call_next(request)

        token = self._extract_token(request)
        if token is None:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload = validate_hs_jwt(token, self.options)
        except JwtValidationError:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        subject = str(payload.get(self.subject_claim) or "").strip()
        if not subject:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        scopes = set()
        scopes.update(_parse_scope_value(payload.get(self.scope_claim)))
        scopes.update(_parse_scope_value(payload.get(self.roles_claim)))
        scopes.update(_subject_scopes(subject, self.subject_scope_map))

        _set_auth_state(request, subject=subject, scheme="jwt", scopes=scopes)
        return await call_next(request)


class CombinedAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        api_key_header: str,
        api_key_records: list[ApiKeyRecord],
        jwt_header: str,
        jwt_bearer_prefix: str,
        jwt_options: JwtValidationOptions,
        jwt_subject_claim: str,
        jwt_scope_claim: str,
        jwt_roles_claim: str,
        subject_scope_map: dict[str, tuple[str, ...]] | None = None,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.api_key_header = api_key_header.lower()
        self.api_key_records = api_key_records
        self.jwt_header = jwt_header.lower()
        self.jwt_bearer_prefix = jwt_bearer_prefix.strip().lower()
        self.jwt_options = jwt_options
        self.jwt_subject_claim = jwt_subject_claim
        self.jwt_scope_claim = jwt_scope_claim
        self.jwt_roles_claim = jwt_roles_claim
        self.subject_scope_map = subject_scope_map or {}
        self.exempt_paths = exempt_paths or []

    def _extract_jwt_token(self, request: Request) -> str | None:
        value = str(request.headers.get(self.jwt_header, "")).strip()
        if not value:
            return None
        if " " in value:
            prefix, token = value.split(" ", 1)
            if prefix.strip().lower() != self.jwt_bearer_prefix:
                return None
            token = token.strip()
            return token or None
        return value

    async def dispatch(self, request: Request, call_next):
        if _is_exempt_path(request.url.path, self.exempt_paths):
            return await call_next(request)

        candidate = request.headers.get(self.api_key_header, "")
        record = verify_api_key(candidate, self.api_key_records)
        if record is not None:
            scopes = _subject_scopes(record.subject, self.subject_scope_map)
            _set_auth_state(request, subject=record.subject, scheme="api_key", scopes=scopes)
            return await call_next(request)

        token = self._extract_jwt_token(request)
        if token is None:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload = validate_hs_jwt(token, self.jwt_options)
        except JwtValidationError:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        subject = str(payload.get(self.jwt_subject_claim) or "").strip()
        if not subject:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        scopes = set()
        scopes.update(_parse_scope_value(payload.get(self.jwt_scope_claim)))
        scopes.update(_parse_scope_value(payload.get(self.jwt_roles_claim)))
        scopes.update(_subject_scopes(subject, self.subject_scope_map))
        _set_auth_state(request, subject=subject, scheme="jwt", scopes=scopes)
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


@dataclass(frozen=True, slots=True)
class RbacRule:
    method: str
    path: str
    scopes: tuple[str, ...]


class RbacMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        default_allow: bool,
        policies: tuple[tuple[str, str, tuple[str, ...]], ...],
        subject_scope_map: dict[str, tuple[str, ...]] | None = None,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.default_allow = default_allow
        self.rules = tuple(
            RbacRule(method=method.upper(), path=path, scopes=tuple(scope.lower() for scope in scopes))
            for method, path, scopes in policies
        )
        self.subject_scope_map = subject_scope_map or {}
        self.exempt_paths = exempt_paths or []

    def _resolve_required_scopes(self, request: Request) -> tuple[str, ...] | None:
        method = request.method.upper()
        path = request.url.path
        for rule in self.rules:
            if rule.method != "*" and rule.method != method:
                continue
            if path == rule.path or path.startswith(f"{rule.path}/"):
                return rule.scopes
        return None

    def _effective_scopes(self, request: Request) -> set[str]:
        explicit = getattr(request.state, "auth_scopes", [])
        scopes = {str(item).strip().lower() for item in explicit if str(item).strip()}
        subject = str(getattr(request.state, "auth_subject", "anonymous") or "anonymous")
        scopes.update(_subject_scopes(subject, self.subject_scope_map))
        return scopes

    async def dispatch(self, request: Request, call_next):
        if _is_exempt_path(request.url.path, self.exempt_paths):
            return await call_next(request)
        required = self._resolve_required_scopes(request)
        if required is None:
            if self.default_allow:
                return await call_next(request)
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        scopes = self._effective_scopes(request)
        if "*" in scopes:
            return await call_next(request)
        for scope in required:
            if scope in scopes:
                return await call_next(request)
        return JSONResponse({"error": "Forbidden", "requiredScopes": list(required)}, status_code=403)


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
