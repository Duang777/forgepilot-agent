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
from forgepilot_api.core.security import parse_api_keys
from forgepilot_api.core.security_middleware import (
    ApiKeyAuthMiddleware,
    AuditMiddleware,
    RateLimitMiddleware,
)
from forgepilot_api.core.settings import get_settings
from forgepilot_api.sandbox.manager import stop_all_providers
from forgepilot_api.services.preview_service import stop_all as stop_all_previews
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
        await stop_all_providers()
        logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

    auth_enabled = settings.auth_mode == "api_key"
    auth_records = parse_api_keys(settings.api_keys)
    if auth_enabled and not auth_records:
        logger.warning(
            "auth mode is api_key but FORGEPILOT_API_KEYS is empty; all non-exempt requests will be denied"
        )

    # Middleware execution order is reverse of registration.
    # Register inner layers first and request context last (outermost) to ensure tracing covers all paths.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if auth_enabled:
        app.add_middleware(
            ApiKeyAuthMiddleware,
            header_name=settings.api_key_header,
            records=auth_records,
            exempt_paths=settings.auth_exempt_paths,
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
                "rateLimitEnabled": settings.rate_limit_enabled,
                "rateLimitBackend": settings.rate_limit_backend,
                "auditEnabled": settings.audit_enabled,
                "filesMode": settings.files_mode,
                "filesDangerousEnabled": settings.files_dangerous_enabled,
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
