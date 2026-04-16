from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_csv(raw: str | None, default: list[str]) -> list[str]:
    if raw is None:
        return default
    stripped = raw.strip()
    if not stripped:
        return default
    if stripped == "*":
        return ["*"]
    values = [item.strip() for item in stripped.split(",") if item.strip()]
    return values or default


def _parse_int(raw: str | None, default: int, minimum: int = 1) -> int:
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _parse_scope_tokens(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return default
    stripped = raw.strip()
    if not stripped:
        return default
    if stripped == "*":
        return ("*",)
    values: list[str] = []
    for token in re.split(r"[,\|]", stripped):
        item = token.strip().lower()
        if not item:
            continue
        if item == "*":
            return ("*",)
        if item not in values:
            values.append(item)
    return tuple(values) if values else default


def _parse_subject_acl(raw: str | None) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    stripped = raw.strip()
    if not stripped:
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for entry in stripped.split(";"):
        item = entry.strip()
        if not item:
            continue
        if "=" not in item:
            continue
        subject, scopes_raw = item.split("=", 1)
        key = subject.strip().lower()
        if not key:
            continue
        scopes = _parse_scope_tokens(scopes_raw, ())
        if scopes:
            out[key] = scopes
    return out


def _parse_rbac_policies(raw: str | None) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if raw is None:
        return ()
    stripped = raw.strip()
    if not stripped:
        return ()
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for entry in stripped.split(";"):
        item = entry.strip()
        if not item or "=" not in item or ":" not in item:
            continue
        lhs, scopes_raw = item.split("=", 1)
        method_raw, path_raw = lhs.split(":", 1)
        method = method_raw.strip().upper()
        path = path_raw.strip()
        if not method:
            continue
        if not path:
            continue
        if not path.startswith("/"):
            path = f"/{path}"
        scopes = _parse_scope_tokens(scopes_raw, ())
        if not scopes:
            continue
        out.append((method, path, scopes))
    return tuple(out)


def _resolve_files_mode(raw_mode: str | None, node_env: str | None) -> str:
    if raw_mode:
        lowered = raw_mode.strip().lower()
        if lowered in {"dev", "development"}:
            return "dev"
        if lowered in {"prod", "production"}:
            return "prod"
    if (node_env or "").strip().lower() == "production":
        return "prod"
    return "dev"


def _resolve_runtime_state_backend(raw_backend: str | None) -> str:
    backend = (raw_backend or "sqlite").strip().lower()
    if backend not in {"sqlite", "redis"}:
        return "sqlite"
    return backend


def _default_cors_origins(node_env: str | None) -> list[str]:
    if (node_env or "").strip().lower() == "production":
        return [
            "http://localhost",
            "http://127.0.0.1",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ]
    return ["*"]


@dataclass(frozen=True, slots=True)
class AppSettings:
    app_name: str
    app_version: str
    cors_origins: list[str]
    cors_allow_credentials: bool
    request_id_header: str
    log_level: str
    expose_metrics: bool
    telemetry_enabled: bool
    telemetry_exporter: str
    telemetry_otlp_endpoint: str | None
    auth_mode: str
    jwt_header: str
    jwt_bearer_prefix: str
    jwt_secret: str
    jwt_algorithms: tuple[str, ...]
    jwt_issuer: str | None
    jwt_audience: str | None
    jwt_subject_claim: str
    jwt_scope_claim: str
    jwt_roles_claim: str
    api_key_header: str
    api_keys: list[str]
    auth_exempt_paths: list[str]
    auth_subject_scopes: dict[str, tuple[str, ...]]
    rate_limit_enabled: bool
    rate_limit_requests: int
    rate_limit_window_seconds: int
    rate_limit_backend: str
    rate_limit_redis_url: str
    rate_limit_redis_key_prefix: str
    rate_limit_fail_open: bool
    rate_limit_trust_proxy: bool
    rate_limit_proxy_header: str
    audit_enabled: bool
    files_mode: str
    files_dangerous_enabled: bool
    files_acl_default: tuple[str, ...]
    files_acl_subjects: dict[str, tuple[str, ...]]
    rbac_enabled: bool
    rbac_default_allow: bool
    rbac_policies: tuple[tuple[str, str, tuple[str, ...]], ...]
    rbac_subject_scopes: dict[str, tuple[str, ...]]
    runtime_state_backend: str
    runtime_state_redis_url: str
    runtime_state_redis_key_prefix: str
    runtime_state_fail_open: bool


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    auth_mode = os.getenv("FORGEPILOT_AUTH_MODE", "off").strip().lower() or "off"
    if auth_mode not in {"off", "api_key", "jwt", "api_key_or_jwt"}:
        auth_mode = "off"

    api_keys = _parse_csv(
        os.getenv("FORGEPILOT_API_KEYS"),
        _parse_csv(os.getenv("FORGEPILOT_API_KEY"), []),
    )
    files_mode = _resolve_files_mode(os.getenv("FORGEPILOT_FILES_MODE"), os.getenv("NODE_ENV"))
    default_files_acl = ("files.read",) if files_mode == "prod" else ("*",)
    default_cors_origins = _default_cors_origins(os.getenv("NODE_ENV"))

    return AppSettings(
        app_name=os.getenv("FORGEPILOT_APP_NAME", "forgepilot-agent-api"),
        app_version=os.getenv("FORGEPILOT_APP_VERSION", "0.1.1"),
        cors_origins=_parse_csv(os.getenv("FORGEPILOT_CORS_ORIGINS"), default_cors_origins),
        cors_allow_credentials=_parse_bool(os.getenv("FORGEPILOT_CORS_ALLOW_CREDENTIALS"), True),
        request_id_header=os.getenv("FORGEPILOT_REQUEST_ID_HEADER", "x-request-id").strip().lower(),
        log_level=os.getenv("FORGEPILOT_LOG_LEVEL", "INFO").strip().upper(),
        expose_metrics=_parse_bool(os.getenv("FORGEPILOT_EXPOSE_METRICS"), True),
        telemetry_enabled=_parse_bool(os.getenv("FORGEPILOT_OTEL_ENABLED"), False),
        telemetry_exporter=os.getenv("FORGEPILOT_OTEL_EXPORTER", "console").strip().lower(),
        telemetry_otlp_endpoint=(os.getenv("FORGEPILOT_OTEL_OTLP_ENDPOINT", "").strip() or None),
        auth_mode=auth_mode,
        jwt_header=os.getenv("FORGEPILOT_JWT_HEADER", "authorization").strip().lower(),
        jwt_bearer_prefix=os.getenv("FORGEPILOT_JWT_BEARER_PREFIX", "bearer").strip().lower(),
        jwt_secret=os.getenv("FORGEPILOT_JWT_SECRET", "").strip(),
        jwt_algorithms=tuple(
            item.upper()
            for item in _parse_csv(os.getenv("FORGEPILOT_JWT_ALGORITHMS"), ["HS256"])
            if item.strip()
        )
        or ("HS256",),
        jwt_issuer=(os.getenv("FORGEPILOT_JWT_ISSUER", "").strip() or None),
        jwt_audience=(os.getenv("FORGEPILOT_JWT_AUDIENCE", "").strip() or None),
        jwt_subject_claim=os.getenv("FORGEPILOT_JWT_SUBJECT_CLAIM", "sub").strip(),
        jwt_scope_claim=os.getenv("FORGEPILOT_JWT_SCOPE_CLAIM", "scope").strip(),
        jwt_roles_claim=os.getenv("FORGEPILOT_JWT_ROLES_CLAIM", "roles").strip(),
        api_key_header=os.getenv("FORGEPILOT_API_KEY_HEADER", "x-api-key").strip().lower(),
        api_keys=api_keys,
        auth_exempt_paths=_parse_csv(
            os.getenv("FORGEPILOT_AUTH_EXEMPT_PATHS"),
            ["/", "/health", "/metrics", "/docs", "/redoc", "/openapi.json"],
        ),
        auth_subject_scopes=_parse_subject_acl(os.getenv("FORGEPILOT_AUTH_SUBJECT_SCOPES")),
        rate_limit_enabled=_parse_bool(os.getenv("FORGEPILOT_RATE_LIMIT_ENABLED"), False),
        rate_limit_requests=_parse_int(os.getenv("FORGEPILOT_RATE_LIMIT_REQUESTS"), 60, minimum=1),
        rate_limit_window_seconds=_parse_int(os.getenv("FORGEPILOT_RATE_LIMIT_WINDOW_SECONDS"), 60, minimum=1),
        rate_limit_backend=os.getenv("FORGEPILOT_RATE_LIMIT_BACKEND", "memory").strip().lower(),
        rate_limit_redis_url=os.getenv("FORGEPILOT_RATE_LIMIT_REDIS_URL", "redis://127.0.0.1:6379/0").strip(),
        rate_limit_redis_key_prefix=os.getenv("FORGEPILOT_RATE_LIMIT_REDIS_KEY_PREFIX", "forgepilot:ratelimit").strip(),
        rate_limit_fail_open=_parse_bool(os.getenv("FORGEPILOT_RATE_LIMIT_FAIL_OPEN"), True),
        rate_limit_trust_proxy=_parse_bool(os.getenv("FORGEPILOT_RATE_LIMIT_TRUST_PROXY"), False),
        rate_limit_proxy_header=os.getenv("FORGEPILOT_RATE_LIMIT_PROXY_HEADER", "x-forwarded-for").strip().lower(),
        audit_enabled=_parse_bool(os.getenv("FORGEPILOT_AUDIT_LOG_ENABLED"), True),
        files_mode=files_mode,
        files_dangerous_enabled=_parse_bool(
            os.getenv("FORGEPILOT_FILES_DANGEROUS_ENABLED"),
            files_mode != "prod",
        ),
        files_acl_default=_parse_scope_tokens(os.getenv("FORGEPILOT_FILES_ACL_DEFAULT"), default_files_acl),
        files_acl_subjects=_parse_subject_acl(os.getenv("FORGEPILOT_FILES_ACL_SUBJECTS")),
        rbac_enabled=_parse_bool(os.getenv("FORGEPILOT_RBAC_ENABLED"), False),
        rbac_default_allow=_parse_bool(os.getenv("FORGEPILOT_RBAC_DEFAULT_ALLOW"), True),
        rbac_policies=_parse_rbac_policies(os.getenv("FORGEPILOT_RBAC_POLICIES")),
        rbac_subject_scopes=_parse_subject_acl(os.getenv("FORGEPILOT_RBAC_SUBJECT_SCOPES")),
        runtime_state_backend=_resolve_runtime_state_backend(os.getenv("FORGEPILOT_RUNTIME_STATE_BACKEND")),
        runtime_state_redis_url=os.getenv(
            "FORGEPILOT_RUNTIME_STATE_REDIS_URL",
            "redis://127.0.0.1:6379/1",
        ).strip(),
        runtime_state_redis_key_prefix=os.getenv(
            "FORGEPILOT_RUNTIME_STATE_REDIS_KEY_PREFIX",
            "forgepilot:runtime",
        ).strip(),
        runtime_state_fail_open=_parse_bool(os.getenv("FORGEPILOT_RUNTIME_STATE_FAIL_OPEN"), True),
    )


def reset_settings_cache() -> None:
    get_settings.cache_clear()
