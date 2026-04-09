from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from forgepilot_api.core.metrics import get_metrics_registry


async def sse_event_stream(generator: AsyncGenerator[dict, None]) -> AsyncGenerator[str, None]:
    metrics = get_metrics_registry()
    metrics.record_sse_started()
    disconnected = False
    try:
        async for item in generator:
            event_type = str(item.get("type") or "")
            if event_type == "tool_use":
                metrics.record_tool_use(str(item.get("name") or "unknown"))
            elif event_type == "tool_result":
                metrics.record_tool_result(str(item.get("name") or "unknown"), bool(item.get("isError")))
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        disconnected = True
        metrics.record_sse_disconnected()
        raise
    except Exception as exc:
        payload = {"type": "error", "message": str(exc)}
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    finally:
        if not disconnected:
            metrics.record_sse_completed()


SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
