from __future__ import annotations

import json
from collections.abc import AsyncGenerator


async def sse_event_stream(generator: AsyncGenerator[dict, None]) -> AsyncGenerator[str, None]:
    try:
        async for item in generator:
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    except Exception as exc:
        payload = {"type": "error", "message": str(exc)}
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

