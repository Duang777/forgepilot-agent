from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from forgepilot_api.core.metrics import get_metrics_registry
from forgepilot_api.core.telemetry import add_span_event, start_span


async def sse_event_stream(generator: AsyncGenerator[dict, None]) -> AsyncGenerator[str, None]:
    metrics = get_metrics_registry()
    metrics.record_sse_started()
    disconnected = False
    with start_span("sse.stream") as span:
        try:
            async for item in generator:
                event_type = str(item.get("type") or "")
                if event_type == "tool_use":
                    tool_name = str(item.get("name") or "unknown")
                    metrics.record_tool_use(tool_name)
                    add_span_event(span, "tool.use", {"tool.name": tool_name})
                elif event_type == "tool_result":
                    tool_name = str(item.get("name") or "unknown")
                    is_error = bool(item.get("isError"))
                    metrics.record_tool_result(tool_name, is_error)
                    add_span_event(
                        span,
                        "tool.result",
                        {"tool.name": tool_name, "tool.is_error": is_error},
                    )
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            disconnected = True
            metrics.record_sse_disconnected()
            add_span_event(span, "sse.disconnected")
            raise
        except Exception as exc:
            payload = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            add_span_event(span, "sse.error", {"error.message": str(exc)})
        finally:
            if not disconnected:
                metrics.record_sse_completed()
                add_span_event(span, "sse.completed")


SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
