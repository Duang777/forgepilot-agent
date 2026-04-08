from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from forgepilot_api.core.metrics import get_metrics_registry

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    body = get_metrics_registry().render_prometheus()
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")
