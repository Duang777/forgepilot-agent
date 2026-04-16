from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forgepilot_api.api.audit import router as audit_router
from forgepilot_api.api.agent import router as agent_router
from forgepilot_api.api.files import router as files_router
from forgepilot_api.api.health import router as health_router
from forgepilot_api.api.mcp import router as mcp_router
from forgepilot_api.api.metrics import router as metrics_router
from forgepilot_api.api.preview import router as preview_router
from forgepilot_api.api.providers import router as providers_router
from forgepilot_api.api.sandbox import router as sandbox_router
from forgepilot_api.core.logging import configure_logging, get_logger
from forgepilot_api.core.middleware import RequestContextMiddleware
from forgepilot_api.core.jwt_auth import JwtValidationOptions
from forgepilot_api.core.security import parse_api_keys
from forgepilot_api.core.security_middleware import (
    ApiKeyAuthMiddleware,
    AuditMiddleware,
    CombinedAuthMiddleware,
    JwtAuthMiddleware,
    RateLimitMiddleware,
    RbacMiddleware,
)
from forgepilot_api.core.settings import get_settings
from forgepilot_api.core.telemetry import configure_telemetry
from forgepilot_api.sandbox.manager import stop_all_providers
from forgepilot_api.services.preview_service import stop_all as stop_all_previews
from forgepilot_api.services.provider_service import shutdown_provider_manager
from forgepilot_api.services.runtime_state_service import reset_runtime_state_backend_cache
from forgepilot_api.storage.db import init_db

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logger.info(
        "starting app name=%s version=%s metrics=%s",
        settings.app_name,
        settings.app_version,
        settings.expose_metrics,
    )
    await init_db()
    try:
        yield
    finally:
        await reset_runtime_state_backend_cache()
        await stop_all_previews()
        await shutdown_provider_manager()
        await stop_all_providers()
        logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_telemetry(
        enabled=settings.telemetry_enabled,
        service_name=settings.app_name,
        exporter=settings.telemetry_exporter,
        otlp_endpoint=settings.telemetry_otlp_endpoint,
    )

    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

    auth_enabled = settings.auth_mode in {"api_key", "jwt", "api_key_or_jwt"}
    auth_records = parse_api_keys(settings.api_keys)
    jwt_options = JwtValidationOptions(
        secret=settings.jwt_secret,
        algorithms=settings.jwt_algorithms,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    if settings.auth_mode == "api_key" and auth_enabled and not auth_records:
        logger.warning(
            "auth mode is api_key but FORGEPILOT_API_KEYS is empty; all non-exempt requests will be denied"
        )
    if settings.auth_mode in {"jwt", "api_key_or_jwt"} and not settings.jwt_secret:
        logger.warning("jwt auth mode enabled but FORGEPILOT_JWT_SECRET is empty; jwt validation will fail")

    # Middleware execution order is reverse of registration.
    # Register inner layers first and request context last (outermost) to ensure tracing covers all paths.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            max_requests=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
            backend=settings.rate_limit_backend,
            redis_url=settings.rate_limit_redis_url,
            redis_key_prefix=settings.rate_limit_redis_key_prefix,
            fail_open=settings.rate_limit_fail_open,
            trust_proxy=settings.rate_limit_trust_proxy,
            proxy_header=settings.rate_limit_proxy_header,
            exempt_paths=settings.auth_exempt_paths,
        )
    if settings.rbac_enabled:
        app.add_middleware(
            RbacMiddleware,
            default_allow=settings.rbac_default_allow,
            policies=settings.rbac_policies,
            subject_scope_map=settings.rbac_subject_scopes,
            exempt_paths=settings.auth_exempt_paths,
        )
    if auth_enabled and settings.auth_mode == "api_key":
        app.add_middleware(
            ApiKeyAuthMiddleware,
            header_name=settings.api_key_header,
            records=auth_records,
            subject_scope_map=settings.auth_subject_scopes,
            exempt_paths=settings.auth_exempt_paths,
        )
    if auth_enabled and settings.auth_mode == "jwt":
        app.add_middleware(
            JwtAuthMiddleware,
            header_name=settings.jwt_header,
            bearer_prefix=settings.jwt_bearer_prefix,
            options=jwt_options,
            subject_claim=settings.jwt_subject_claim,
            scope_claim=settings.jwt_scope_claim,
            roles_claim=settings.jwt_roles_claim,
            subject_scope_map=settings.auth_subject_scopes,
            exempt_paths=settings.auth_exempt_paths,
        )
    if auth_enabled and settings.auth_mode == "api_key_or_jwt":
        app.add_middleware(
            CombinedAuthMiddleware,
            api_key_header=settings.api_key_header,
            api_key_records=auth_records,
            jwt_header=settings.jwt_header,
            jwt_bearer_prefix=settings.jwt_bearer_prefix,
            jwt_options=jwt_options,
            jwt_subject_claim=settings.jwt_subject_claim,
            jwt_scope_claim=settings.jwt_scope_claim,
            jwt_roles_claim=settings.jwt_roles_claim,
            subject_scope_map=settings.auth_subject_scopes,
            exempt_paths=settings.auth_exempt_paths,
        )
    if settings.rate_limit_enabled:
        logger.info(
            "rate limit enabled backend=%s requests=%s window_seconds=%s fail_open=%s",
            settings.rate_limit_backend,
            settings.rate_limit_requests,
            settings.rate_limit_window_seconds,
            settings.rate_limit_fail_open,
        )
    if settings.audit_enabled:
        app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestContextMiddleware, request_id_header=settings.request_id_header)

    @app.get("/")
    async def root() -> dict[str, object]:
        return {
            "name": "ForgePilot Agent API",
            "version": settings.app_version,
            "security": {
                "authMode": settings.auth_mode,
                "rbacEnabled": settings.rbac_enabled,
                "rateLimitEnabled": settings.rate_limit_enabled,
                "rateLimitBackend": settings.rate_limit_backend,
                "auditEnabled": settings.audit_enabled,
                "filesMode": settings.files_mode,
                "filesDangerousEnabled": settings.files_dangerous_enabled,
                "telemetryEnabled": settings.telemetry_enabled,
            },
            "endpoints": {
                "health": "/health",
                "metrics": "/metrics" if settings.expose_metrics else "disabled",
                "agent": "/agent",
                "sandbox": "/sandbox",
                "preview": "/preview",
                "providers": "/providers",
                "files": "/files",
                "mcp": "/mcp",
                "audit": "/audit/logs",
            },
        }

    app.include_router(health_router)
    app.include_router(agent_router)
    app.include_router(sandbox_router)
    app.include_router(preview_router)
    app.include_router(providers_router)
    app.include_router(files_router)
    app.include_router(mcp_router)
    app.include_router(audit_router)
    if settings.expose_metrics:
        app.include_router(metrics_router)

    return app


app = create_app()
