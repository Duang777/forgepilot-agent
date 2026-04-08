from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(slots=True)
class _RequestAggregate:
    count: int = 0
    total_ms: float = 0.0


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._requests_total = 0
        self._errors_total = 0
        self._by_route: Dict[Tuple[str, str, int], _RequestAggregate] = {}

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

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            by_route = {
                f"{method} {path} {status}": {
                    "count": agg.count,
                    "avgMs": round(agg.total_ms / agg.count, 3) if agg.count else 0.0,
                }
                for (method, path, status), agg in self._by_route.items()
            }
            return {
                "requestsTotal": self._requests_total,
                "errorsTotal": self._errors_total,
                "uptimeSec": round(max(0.0, time.time() - self._start_time), 3),
                "routes": by_route,
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
        ]
        routes = snap["routes"]
        assert isinstance(routes, dict)
        for key in sorted(routes.keys()):
            method, path, status = key.split(" ", 2)
            info = routes[key]
            assert isinstance(info, dict)
            count = int(info["count"])
            avg_ms = float(info["avgMs"])
            labels = f'method="{method}",path="{path}",status="{status}"'
            lines.append(f"forgepilot_http_route_requests{{{labels}}} {count}")
            lines.append(f"forgepilot_http_route_latency_ms_avg{{{labels}}} {avg_ms}")
        return "\n".join(lines) + "\n"


_REGISTRY = MetricsRegistry()


def get_metrics_registry() -> MetricsRegistry:
    return _REGISTRY
