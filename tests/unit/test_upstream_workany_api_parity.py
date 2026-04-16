from __future__ import annotations

import re
from pathlib import Path

import pytest

from forgepilot_api.app import create_app


_WORKANY_API_PREFIXES = {
    "agent.ts": "/agent",
    "health.ts": "/health",
    "sandbox.ts": "/sandbox",
    "preview.ts": "/preview",
    "providers.ts": "/providers",
    "files.ts": "/files",
    "mcp.ts": "/mcp",
}


def _canonical_route_path(path: str) -> str:
    path = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", "{}", path)
    path = re.sub(r"\{[^{}]+\}", "{}", path)
    return path


def _collect_workany_routes(upstream_root: Path) -> set[str]:
    routes: set[str] = set()
    api_dir = upstream_root / "src-api" / "src" / "app" / "api"

    for file_name, prefix in _WORKANY_API_PREFIXES.items():
        source = (api_dir / file_name).read_text(encoding="utf-8")
        for match in re.finditer(r"\b(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]*)['\"]", source):
            method = match.group(1).upper()
            route = match.group(2)
            full_path = prefix + ("" if route == "/" else route)
            routes.add(f"{method} {_canonical_route_path(full_path)}")
    return routes


def _collect_forgepilot_routes() -> set[str]:
    app = create_app()
    routes: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.add(f"{method} {_canonical_route_path(path)}")
    return routes


def test_workany_api_routes_are_covered_by_python_api() -> None:
    upstream_root = Path(r"D:\DUAN\APP\_upstream\workany")
    if not upstream_root.exists():
        pytest.skip("workany upstream repository not found")

    upstream_routes = _collect_workany_routes(upstream_root)
    python_routes = _collect_forgepilot_routes()
    missing = sorted(upstream_routes - python_routes)
    assert missing == []

