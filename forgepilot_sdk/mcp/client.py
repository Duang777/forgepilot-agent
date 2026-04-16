from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from forgepilot_sdk.tools.base import define_tool
from forgepilot_sdk.types import ToolContext, ToolDefinition, ToolResult


class _McpRpcError(RuntimeError):
    pass


class _BaseRpcClient:
    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def list_resources(self) -> list[dict[str, Any]]:
        return []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        raise _McpRpcError("readResource is not supported by this server")

    async def close(self) -> None:
        return None


class _HttpRpcClient(_BaseRpcClient):
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {}
        self.client = httpx.AsyncClient(timeout=60, follow_redirects=True)
        self._request_id = 0

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        response = await self.client.post(self.url, json=payload, headers=self.headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise _McpRpcError(f"Invalid MCP response: {data}")
        if "error" in data:
            err = data["error"]
            raise _McpRpcError(str(err.get("message") if isinstance(err, dict) else err))
        result = data.get("result")
        if not isinstance(result, dict):
            return {"value": result}
        return result

    async def initialize(self) -> None:
        try:
            await self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "open-agent-sdk-py", "version": "1.0.0"},
                    "capabilities": {},
                },
            )
            await self._request("notifications/initialized", {})
        except Exception:
            # Some servers do not require initialization.
            return None

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        return result

    async def list_resources(self) -> list[dict[str, Any]]:
        try:
            result = await self._request("resources/list", {})
        except Exception:
            return []
        resources = result.get("resources")
        return resources if isinstance(resources, list) else []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        result = await self._request("resources/read", {"uri": uri})
        return result

    async def close(self) -> None:
        await self.client.aclose()


class _SseRpcClient(_BaseRpcClient):
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {}
        self.client = httpx.AsyncClient(timeout=60, follow_redirects=True)
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stream_cm: Any | None = None
        self._stream_response: httpx.Response | None = None
        self._message_endpoint: str | None = None
        self._ready_event = asyncio.Event()

    async def _ensure_stream(self) -> None:
        if self._reader_task and not self._reader_task.done():
            return

        request_headers = {"Accept": "text/event-stream", **self.headers}
        self._stream_cm = self.client.stream("GET", self.url, headers=request_headers)
        response = await self._stream_cm.__aenter__()
        response.raise_for_status()
        self._stream_response = response
        self._reader_task = asyncio.create_task(self._reader_loop(response))

    async def _reader_loop(self, response: httpx.Response) -> None:
        event_name = "message"
        data_lines: list[str] = []
        try:
            async for line in response.aiter_lines():
                if line is None:
                    continue
                text = line.rstrip("\r")
                if not text:
                    await self._dispatch_event(event_name, "\n".join(data_lines))
                    event_name = "message"
                    data_lines = []
                    continue
                if text.startswith(":"):
                    continue
                if text.startswith("event:"):
                    event_name = text[6:].strip() or "message"
                    continue
                if text.startswith("data:"):
                    data_lines.append(text[5:].lstrip())
                    continue
            # Flush trailing event if stream closed without blank line.
            if data_lines:
                await self._dispatch_event(event_name, "\n".join(data_lines))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            for pending in self._pending.values():
                if not pending.done():
                    pending.set_exception(exc)
            self._pending.clear()

    async def _dispatch_event(self, event_name: str, data: str) -> None:
        payload = data.strip()
        if not payload:
            return

        lowered = event_name.lower()
        if lowered in {"endpoint", "mcp_endpoint", "session"}:
            self._message_endpoint = urljoin(self.url, payload)
            self._ready_event.set()
            return

        message: dict[str, Any] | None = None
        try:
            candidate = json.loads(payload)
            if isinstance(candidate, dict):
                message = candidate
        except Exception:
            message = None

        if message is None:
            # Some servers emit endpoint in data body without event name.
            if payload.startswith("http://") or payload.startswith("https://") or payload.startswith("/"):
                self._message_endpoint = urljoin(self.url, payload)
                self._ready_event.set()
            return

        if "id" in message:
            try:
                message_id = int(message["id"])
            except Exception:
                return
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(message)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        await self._ensure_stream()

        if self._message_endpoint is None:
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                # Some SSE servers accept requests on the same endpoint.
                self._message_endpoint = self.url

        endpoint = self._message_endpoint or self.url
        response = await self.client.post(endpoint, json=payload, headers=self.headers)
        response.raise_for_status()
        if not response.content:
            return None

        try:
            parsed = response.json()
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[request_id] = fut
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        immediate = await self._post(payload)
        if immediate and "id" in immediate:
            pending = self._pending.pop(request_id, None)
            if pending is not None and not pending.done():
                pending.set_result(immediate)

        response = await asyncio.wait_for(fut, timeout=60)
        if not isinstance(response, dict):
            raise _McpRpcError(f"Invalid MCP response for {method}")
        if "error" in response:
            err = response["error"]
            raise _McpRpcError(str(err.get("message") if isinstance(err, dict) else err))
        result = response.get("result")
        if not isinstance(result, dict):
            return {"value": result}
        return result

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        await self._post(payload)

    async def initialize(self) -> None:
        await self._ensure_stream()
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "open-agent-sdk-py", "version": "1.0.0"},
                "capabilities": {},
            },
        )
        try:
            await self._notify("notifications/initialized", {})
        except Exception:
            return None

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": arguments})

    async def list_resources(self) -> list[dict[str, Any]]:
        try:
            result = await self._request("resources/list", {})
        except Exception:
            return []
        resources = result.get("resources")
        return resources if isinstance(resources, list) else []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return await self._request("resources/read", {"uri": uri})

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except Exception:
                pass
        if self._stream_cm is not None:
            try:
                await self._stream_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._stream_cm = None
            self._stream_response = None
        await self.client.aclose()


class _StdioRpcClient(_BaseRpcClient):
    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self._request_id = 0
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}

    async def _ensure_process(self) -> None:
        if self._process and self._process.returncode is None:
            return

        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _stderr_loop(self) -> None:
        process = self._process
        if not process or not process.stderr:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return

    async def _reader_loop(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return

        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        break
                    if ":" in text:
                        key, value = text.split(":", 1)
                        headers[key.strip().lower()] = value.strip()

                content_length = int(headers.get("content-length", "0") or "0")
                if content_length <= 0:
                    continue

                body = await process.stdout.readexactly(content_length)
                message = json.loads(body.decode("utf-8", errors="replace"))
                if not isinstance(message, dict):
                    continue

                if "id" in message:
                    message_id = int(message["id"])
                    future = self._pending.pop(message_id, None)
                    if future is not None and not future.done():
                        future.set_result(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            for pending in self._pending.values():
                if not pending.done():
                    pending.set_exception(exc)
            self._pending.clear()

    async def _send(self, payload: dict[str, Any]) -> None:
        await self._ensure_process()
        process = self._process
        if not process or not process.stdin:
            raise _McpRpcError("MCP process is not running")

        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        framed = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw
        process.stdin.write(framed)
        await process.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._ensure_process()
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[request_id] = fut

        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

        response = await asyncio.wait_for(fut, timeout=60)
        if not isinstance(response, dict):
            raise _McpRpcError(f"Invalid MCP response for {method}")
        if "error" in response:
            err = response["error"]
            raise _McpRpcError(str(err.get("message") if isinstance(err, dict) else err))
        result = response.get("result")
        if not isinstance(result, dict):
            return {"value": result}
        return result

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            }
        )

    async def initialize(self) -> None:
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "open-agent-sdk-py", "version": "1.0.0"},
                "capabilities": {},
            },
        )
        try:
            await self._notify("notifications/initialized", {})
        except Exception:
            return None

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        return result

    async def list_resources(self) -> list[dict[str, Any]]:
        try:
            result = await self._request("resources/list", {})
        except Exception:
            return []
        resources = result.get("resources")
        return resources if isinstance(resources, list) else []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        result = await self._request("resources/read", {"uri": uri})
        return result

    async def close(self) -> None:
        process = self._process
        if process is None:
            return

        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()

        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


@dataclass(slots=True)
class MCPConnection:
    name: str
    config: dict[str, Any]
    status: str = "connected"
    tools: list[ToolDefinition] = field(default_factory=list)
    _client: _BaseRpcClient | None = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def list_resources(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        return await self._client.list_resources()

    async def read_resource(self, uri: str) -> dict[str, Any]:
        if self._client is None:
            raise _McpRpcError("MCP client is not connected")
        return await self._client.read_resource(uri)


_ACTIVE_CONNECTIONS: list[MCPConnection] = []


def load_mcp_servers_from_file(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    raw = data.get("mcpServers", data)
    return raw if isinstance(raw, dict) else {}


def load_default_mcp_servers() -> dict[str, dict[str, Any]]:
    path = Path.home() / ".forgepilot" / "mcp.json"
    return load_mcp_servers_from_file(path)


def _extract_mcp_output(result: dict[str, Any]) -> tuple[str, bool]:
    is_error = bool(result.get("isError") or result.get("is_error"))
    content = result.get("content")

    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
            else:
                chunks.append(json.dumps(block, ensure_ascii=False))
        text = "\n".join([c for c in chunks if c]).strip()
        return text or json.dumps(result, ensure_ascii=False), is_error

    if content is None:
        return json.dumps(result, ensure_ascii=False), is_error

    return str(content), is_error


def _build_mcp_tool_definition(server_name: str, tool_desc: dict[str, Any], client: _BaseRpcClient) -> ToolDefinition:
    raw_name = str(tool_desc.get("name") or "")
    tool_name = f"mcp__{server_name}__{raw_name}"
    description = str(tool_desc.get("description") or f"MCP tool: {raw_name} from {server_name}")
    input_schema = tool_desc.get("inputSchema")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}

    async def _call(input_data: dict[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx
        try:
            result = await client.call_tool(raw_name, input_data)
            output, is_error = _extract_mcp_output(result)
            return ToolResult(content=output, is_error=is_error)
        except Exception as exc:
            return ToolResult(content=f"MCP tool error: {exc}", is_error=True)

    return define_tool(
        name=tool_name,
        description=description,
        input_schema=input_schema,
        call=_call,
        read_only=False,
        concurrency_safe=False,
    )


async def connect_mcp_server(name: str, config: dict[str, Any]) -> MCPConnection:
    client: _BaseRpcClient

    try:
        transport_type = str(config.get("type") or "stdio").lower()
        if transport_type == "http":
            url = str(config.get("url") or "").strip()
            if not url:
                raise _McpRpcError(f"MCP server '{name}' missing url")
            headers = config.get("headers") if isinstance(config.get("headers"), dict) else None
            client = _HttpRpcClient(url=url, headers=headers)
        elif transport_type == "sse":
            url = str(config.get("url") or "").strip()
            if not url:
                raise _McpRpcError(f"MCP server '{name}' missing url")
            headers = config.get("headers") if isinstance(config.get("headers"), dict) else None
            client = _SseRpcClient(url=url, headers=headers)
        else:
            command = str(config.get("command") or "").strip()
            if not command:
                raise _McpRpcError(f"MCP server '{name}' missing command")
            args = config.get("args") if isinstance(config.get("args"), list) else []
            env = config.get("env") if isinstance(config.get("env"), dict) else None
            client = _StdioRpcClient(command=command, args=[str(x) for x in args], env=env)

        await client.initialize()
        mcp_tools = await client.list_tools()
        tools = [_build_mcp_tool_definition(name, tool, client) for tool in mcp_tools if isinstance(tool, dict)]

        connection = MCPConnection(
            name=name,
            config=config,
            status="connected",
            tools=tools,
            _client=client,
        )
        _ACTIVE_CONNECTIONS.append(connection)
        return connection
    except Exception:
        failed = MCPConnection(name=name, config=config, status="error", tools=[], _client=None)
        _ACTIVE_CONNECTIONS.append(failed)
        return failed


async def close_all_connections(connections: list[MCPConnection] | None = None) -> None:
    if connections is None:
        targets = list(_ACTIVE_CONNECTIONS)
    else:
        targets = list(connections)

    for conn in targets:
        try:
            await conn.close()
        except Exception:
            pass

    if connections is None:
        _ACTIVE_CONNECTIONS.clear()
    else:
        for conn in connections:
            if conn in _ACTIVE_CONNECTIONS:
                _ACTIVE_CONNECTIONS.remove(conn)


async def connectMCPServer(name: str, config: dict[str, Any]) -> MCPConnection:
    return await connect_mcp_server(name, config)


async def closeAllConnections(connections: list[MCPConnection] | None = None) -> None:
    await close_all_connections(connections)


def loadMcpServersFromFile(path: Path) -> dict[str, dict[str, Any]]:
    return load_mcp_servers_from_file(path)


def loadDefaultMcpServers() -> dict[str, dict[str, Any]]:
    return load_default_mcp_servers()


