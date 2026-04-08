from __future__ import annotations

import asyncio
from pathlib import Path

from forgepilot_sdk.mcp import client as mcp_client
from forgepilot_sdk.types import ToolContext


class _FakeSseClient:
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {}
        self.closed = False

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> list[dict]:
        return [
            {
                "name": "ping",
                "description": "Ping tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        assert name == "ping"
        del arguments
        return {"content": [{"type": "text", "text": "pong"}]}

    async def list_resources(self) -> list[dict]:
        return []

    async def read_resource(self, uri: str) -> dict:
        del uri
        return {"contents": []}

    async def close(self) -> None:
        self.closed = True


def test_connect_mcp_sse_transport_and_dynamic_tool(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "_SseRpcClient", _FakeSseClient)

    async def _run() -> None:
        connection = await mcp_client.connect_mcp_server(
            "remote",
            {"type": "sse", "url": "https://example.com/sse", "headers": {"x-demo": "1"}},
        )
        assert connection.status == "connected"
        assert len(connection.tools) == 1
        assert connection.tools[0].name == "mcp__remote__ping"

        result = await connection.tools[0].call({}, ToolContext(cwd=Path.cwd()))
        assert result.is_error is False
        assert "pong" in result.content

        await mcp_client.close_all_connections([connection])

    asyncio.run(_run())


def test_connect_mcp_sse_requires_url() -> None:
    async def _run() -> None:
        connection = await mcp_client.connect_mcp_server("remote", {"type": "sse"})
        assert connection.status == "error"
        assert connection.tools == []
        await mcp_client.close_all_connections([connection])

    asyncio.run(_run())

