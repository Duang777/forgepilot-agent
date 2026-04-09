from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(slots=True)
class _RequestAggregate:
    count: int = 0
    total_ms: float = 0.0


@dataclass(slots=True)
class _ToolAggregate:
    uses: int = 0
    errors: int = 0


@dataclass(slots=True)
class _SandboxAggregate:
    total: int = 0
    fallback: int = 0


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._requests_total = 0
        self._errors_total = 0
        self._by_route: Dict[Tuple[str, str, int], _RequestAggregate] = {}
        self._sse_streams_started = 0
        self._sse_streams_completed = 0
        self._sse_streams_disconnected = 0
        self._tool_uses_total = 0
        self._tool_errors_total = 0
        self._tool_by_name: Dict[str, _ToolAggregate] = {}
        self._sandbox_exec_total = 0
        self._sandbox_fallback_total = 0
        self._sandbox_by_provider: Dict[str, _SandboxAggregate] = {}

    def record_request(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        normalized_path = path or "/"
        key = (method.upper(), normalized_path, int(status_code))
        with self._lock:
            self._requests_total += 1
            if status_code >= 500:
                self._errors_total += 1
            aggregate = self._by_route.setdefault(key, _RequestAggregate())
            aggregate.count += 1
            aggregate.total_ms += max(0.0, duration_ms)

    def record_sse_started(self) -> None:
        with self._lock:
            self._sse_streams_started += 1

    def record_sse_completed(self) -> None:
        with self._lock:
            self._sse_streams_completed += 1

    def record_sse_disconnected(self) -> None:
        with self._lock:
            self._sse_streams_disconnected += 1

    def record_tool_use(self, tool_name: str | None) -> None:
        key = (tool_name or "unknown").strip() or "unknown"
        with self._lock:
            self._tool_uses_total += 1
            aggregate = self._tool_by_name.setdefault(key, _ToolAggregate())
            aggregate.uses += 1

    def record_tool_result(self, tool_name: str | None, is_error: bool) -> None:
        if not is_error:
            return
        key = (tool_name or "unknown").strip() or "unknown"
        with self._lock:
            self._tool_errors_total += 1
            aggregate = self._tool_by_name.setdefault(key, _ToolAggregate())
            aggregate.errors += 1

    def record_sandbox_execution(self, provider: str | None, used_fallback: bool) -> None:
        key = (provider or "unknown").strip() or "unknown"
        with self._lock:
            self._sandbox_exec_total += 1
            if used_fallback:
                self._sandbox_fallback_total += 1
            aggregate = self._sandbox_by_provider.setdefault(key, _SandboxAggregate())
            aggregate.total += 1
            if used_fallback:
                aggregate.fallback += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            by_route = {
                f"{method} {path} {status}": {
                    "count": agg.count,
                    "avgMs": round(agg.total_ms / agg.count, 3) if agg.count else 0.0,
                }
                for (method, path, status), agg in self._by_route.items()
            }
            tool_by_name = {
                key: {"uses": agg.uses, "errors": agg.errors}
                for key, agg in self._tool_by_name.items()
            }
            sandbox_by_provider = {
                key: {"total": agg.total, "fallback": agg.fallback}
                for key, agg in self._sandbox_by_provider.items()
            }
            return {
                "requestsTotal": self._requests_total,
                "errorsTotal": self._errors_total,
                "uptimeSec": round(max(0.0, time.time() - self._start_time), 3),
                "routes": by_route,
                "sse": {
                    "started": self._sse_streams_started,
                    "completed": self._sse_streams_completed,
                    "disconnected": self._sse_streams_disconnected,
                },
                "tools": {
                    "usesTotal": self._tool_uses_total,
                    "errorsTotal": self._tool_errors_total,
                    "byName": tool_by_name,
                },
                "sandbox": {
                    "execTotal": self._sandbox_exec_total,
                    "fallbackTotal": self._sandbox_fallback_total,
                    "byProvider": sandbox_by_provider,
                },
            }

    def render_prometheus(self) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP forgepilot_http_requests_total Total HTTP requests handled.",
            "# TYPE forgepilot_http_requests_total counter",
            f"forgepilot_http_requests_total {snap['requestsTotal']}",
            "# HELP forgepilot_http_errors_total Total HTTP requests resulting in 5xx responses.",
            "# TYPE forgepilot_http_errors_total counter",
            f"forgepilot_http_errors_total {snap['errorsTotal']}",
            "# HELP forgepilot_uptime_seconds Process uptime in seconds.",
            "# TYPE forgepilot_uptime_seconds gauge",
            f"forgepilot_uptime_seconds {snap['uptimeSec']}",
            "# HELP forgepilot_http_route_requests Total HTTP requests by method/path/status.",
            "# TYPE forgepilot_http_route_requests counter",
            "# HELP forgepilot_http_route_latency_ms_avg Average request latency by method/path/status.",
            "# TYPE forgepilot_http_route_latency_ms_avg gauge",
            "# HELP forgepilot_sse_streams_started_total Total SSE streams started.",
            "# TYPE forgepilot_sse_streams_started_total counter",
            f"forgepilot_sse_streams_started_total {snap['sse']['started']}",
            "# HELP forgepilot_sse_streams_completed_total Total SSE streams completed.",
            "# TYPE forgepilot_sse_streams_completed_total counter",
            f"forgepilot_sse_streams_completed_total {snap['sse']['completed']}",
            "# HELP forgepilot_sse_streams_disconnected_total Total SSE streams disconnected by clients.",
            "# TYPE forgepilot_sse_streams_disconnected_total counter",
            f"forgepilot_sse_streams_disconnected_total {snap['sse']['disconnected']}",
            "# HELP forgepilot_tool_uses_total Total tool invocations observed in SSE events.",
            "# TYPE forgepilot_tool_uses_total counter",
            f"forgepilot_tool_uses_total {snap['tools']['usesTotal']}",
            "# HELP forgepilot_tool_errors_total Total tool errors observed in SSE events.",
            "# TYPE forgepilot_tool_errors_total counter",
            f"forgepilot_tool_errors_total {snap['tools']['errorsTotal']}",
            "# HELP forgepilot_sandbox_exec_total Total sandbox executions.",
            "# TYPE forgepilot_sandbox_exec_total counter",
            f"forgepilot_sandbox_exec_total {snap['sandbox']['execTotal']}",
            "# HELP forgepilot_sandbox_fallback_total Total sandbox executions that used fallback provider.",
            "# TYPE forgepilot_sandbox_fallback_total counter",
            f"forgepilot_sandbox_fallback_total {snap['sandbox']['fallbackTotal']}",
            "# HELP forgepilot_tool_uses_by_name_total Total tool invocations by tool name.",
            "# TYPE forgepilot_tool_uses_by_name_total counter",
            "# HELP forgepilot_tool_errors_by_name_total Total tool errors by tool name.",
            "# TYPE forgepilot_tool_errors_by_name_total counter",
            "# HELP forgepilot_sandbox_exec_by_provider_total Total sandbox executions by provider.",
            "# TYPE forgepilot_sandbox_exec_by_provider_total counter",
            "# HELP forgepilot_sandbox_fallback_by_provider_total Total sandbox fallback executions by provider.",
            "# TYPE forgepilot_sandbox_fallback_by_provider_total counter",
        ]
        routes = snap["routes"]
        assert isinstance(routes, dict)
        for key in sorted(routes.keys()):
            method, path, status = key.split(" ", 2)
            info = routes[key]
            assert isinstance(info, dict)
            count = int(info["count"])
            avg_ms = float(info["avgMs"])
            labels = (
                f'method="{_escape_label(method)}",'
                f'path="{_escape_label(path)}",'
                f'status="{_escape_label(status)}"'
            )
            lines.append(f"forgepilot_http_route_requests{{{labels}}} {count}")
            lines.append(f"forgepilot_http_route_latency_ms_avg{{{labels}}} {avg_ms}")

        tools = snap["tools"]["byName"]
        assert isinstance(tools, dict)
        for tool_name in sorted(tools.keys()):
            tool_info = tools[tool_name]
            assert isinstance(tool_info, dict)
            labels = f'tool="{_escape_label(str(tool_name))}"'
            lines.append(
                f"forgepilot_tool_uses_by_name_total{{{labels}}} {int(tool_info.get('uses', 0))}"
            )
            lines.append(
                f"forgepilot_tool_errors_by_name_total{{{labels}}} {int(tool_info.get('errors', 0))}"
            )

        sandbox = snap["sandbox"]["byProvider"]
        assert isinstance(sandbox, dict)
        for provider_name in sorted(sandbox.keys()):
            provider_info = sandbox[provider_name]
            assert isinstance(provider_info, dict)
            labels = f'provider="{_escape_label(str(provider_name))}"'
            lines.append(
                "forgepilot_sandbox_exec_by_provider_total"
                f"{{{labels}}} {int(provider_info.get('total', 0))}"
            )
            lines.append(
                "forgepilot_sandbox_fallback_by_provider_total"
                f"{{{labels}}} {int(provider_info.get('fallback', 0))}"
            )
        return "\n".join(lines) + "\n"


_REGISTRY = MetricsRegistry()


def get_metrics_registry() -> MetricsRegistry:
    return _REGISTRY
