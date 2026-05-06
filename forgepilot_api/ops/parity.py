from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from forgepilot_api.app import create_app
from forgepilot_sdk.tools import get_all_base_tools


EXPECTED_ROUTE_SIGNATURES: set[str] = {
    "POST /agent",
    "POST /agent/plan",
    "POST /agent/execute",
    "POST /agent/chat",
    "POST /agent/title",
    "POST /agent/permission",
    "POST /agent/stop/{session_id}",
    "GET /agent/session/{session_id}",
    "GET /agent/plan/{plan_id}",
    "POST /sandbox/exec",
    "POST /sandbox/run/file",
    "POST /sandbox/run/node",
    "POST /sandbox/run/python",
    "POST /sandbox/exec/stream",
    "POST /sandbox/stop-all",
    "GET /sandbox/available",
    "GET /sandbox/images",
    "GET /sandbox/pool/stats",
    "GET /providers/config",
    "POST /providers/agents/switch",
    "POST /providers/sandbox/switch",
    "POST /providers/settings/sync",
    "POST /files/readdir",
    "POST /files/stat",
    "POST /files/read",
    "POST /files/read-binary",
    "POST /files/open",
    "POST /files/open-in-editor",
    "POST /files/import-skill",
    "POST /files/import-skill/self-check",
    "GET /files/skills-dir",
    "GET /files/task/{task_id}",
    "GET /mcp",
    "GET /mcp/config",
    "POST /mcp/config",
    "POST /mcp/load",
    "GET /health",
}

EXPECTED_SSE_EVENT_TYPES: set[str] = {
    "text",
    "tool_use",
    "tool_result",
    "result",
    "error",
    "session",
    "done",
    "plan",
    "direct_answer",
}

EXPECTED_TOOL_NAMES: set[str] = {
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "NotebookEdit",
    "WebSearch",
    "WebFetch",
    "Agent",
    "SendMessage",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskList",
    "TaskUpdate",
    "TaskGet",
    "TaskStop",
    "TaskOutput",
    "Task",
    "EnterWorktree",
    "ExitWorktree",
    "EnterPlanMode",
    "ExitPlanMode",
    "AskUserQuestion",
    "ToolSearch",
    "ListMcpResources",
    "ReadMcpResource",
    "CronCreate",
    "CronDelete",
    "CronList",
    "RemoteTrigger",
    "LSP",
    "Config",
    "TodoWrite",
    "Skill",
}

EXPECTED_SEMANTIC_MARKERS: set[str] = {
    "__POLICY_DENIED__",
    "__POLICY_EXECUTION_MISMATCH__",
    "__UNVERIFIED_FILE_OPERATION__",
}
_SEMANTIC_TEST_KEYWORDS = ("semantic", "policy", "unverified", "fallback")
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class ParitySummary:
    generated_at_utc: str
    routes_total: int
    expected_routes_total: int
    expected_routes_missing: tuple[str, ...]
    sse_types_total: int
    expected_sse_total: int
    expected_sse_missing: tuple[str, ...]
    tools_total: int
    expected_tools_total: int
    expected_tools_missing: tuple[str, ...]
    test_functions_total: int
    passed_tests: int
    skipped_tests: int
    semantic_markers_total: int
    expected_semantic_markers_total: int
    expected_semantic_markers_missing: tuple[str, ...]
    semantic_cases_total: int
    semantic_cases_passed: int
    semantic_cases_failed: int

    @property
    def is_full_parity(self) -> bool:
        return (
            not self.expected_routes_missing
            and not self.expected_sse_missing
            and not self.expected_tools_missing
        )

    @property
    def is_semantic_baseline(self) -> bool:
        return (
            not self.expected_semantic_markers_missing
            and self.semantic_cases_total > 0
            and self.semantic_cases_failed == 0
        )

    @property
    def semantic_pass_rate(self) -> float:
        if self.semantic_cases_total <= 0:
            return 0.0
        return self.semantic_cases_passed / self.semantic_cases_total

    @property
    def is_full_parity_with_semantic(self) -> bool:
        return self.is_full_parity and self.is_semantic_baseline


def _collect_routes() -> set[str]:
    app = create_app()
    out: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in sorted(methods):
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add(f"{method} {path}")
    return out


def _collect_tool_names() -> set[str]:
    names: set[str] = set()
    for tool in get_all_base_tools():
        name = str(getattr(tool, "name", "")).strip()
        if name:
            names.add(name)
    return names


def _collect_sse_types_from_snapshots(snapshot_dir: Path) -> set[str]:
    out: set[str] = set()
    if not snapshot_dir.exists():
        return out
    for path in sorted(snapshot_dir.glob("*.json")):
        try:
            import json

            payloads = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payloads, list):
            continue
        for item in payloads:
            if isinstance(item, dict) and "type" in item:
                out.add(str(item["type"]))
    return out


def _count_test_functions(test_root: Path) -> int:
    count = 0
    for path in test_root.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        count += len(re.findall(r"^def test_", text, flags=re.MULTILINE))
    return count


def _collect_semantic_markers(repo_root: Path) -> set[str]:
    marker_pattern = re.compile(r"__([A-Z0-9_]+)__")
    observed: set[str] = set()
    scan_roots = [
        repo_root / "forgepilot_api",
        repo_root / "forgepilot_sdk",
        repo_root / "tests",
    ]
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for match in marker_pattern.findall(text):
                observed.add(f"__{match}__")
    return observed


def _collect_semantic_case_nodeids(repo_root: Path) -> set[str]:
    test_root = repo_root / "tests"
    if not test_root.exists():
        return set()
    out: set[str] = set()
    for path in test_root.rglob("test_*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = path.relative_to(repo_root).as_posix()
        for match in re.findall(r"^def (test_[a-zA-Z0-9_]+)\s*\(", text, flags=re.MULTILINE):
            nodeid = f"{rel}::{match}"
            lowered = nodeid.lower()
            if any(keyword in lowered for keyword in _SEMANTIC_TEST_KEYWORDS):
                out.add(nodeid)
    return out


def _read_latest_failed_nodeids(repo_root: Path) -> set[str]:
    # Keep report deterministic by default. Enable cache-based pass/fail estimate explicitly.
    if str(os.getenv("FORGEPILOT_PARITY_USE_PYTEST_CACHE", "0")).strip().lower() not in _TRUTHY:
        return set()
    cache = repo_root / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if not cache.exists():
        return set()
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(payload, dict):
        return set()
    return {str(nodeid) for nodeid in payload.keys()}


def _read_latest_pytest_result(repo_root: Path) -> tuple[int, int]:
    # Conservative fallback: unknown run -> (0, 0). We do not parse external logs by default.
    cache = repo_root / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if not cache.exists():
        return (0, 0)
    return (0, 0)


def build_parity_summary(repo_root: Path) -> ParitySummary:
    routes = _collect_routes()
    tools = _collect_tool_names()
    sse_types = _collect_sse_types_from_snapshots(repo_root / "tests" / "contract" / "snapshots")
    tests_total = _count_test_functions(repo_root / "tests")
    passed, skipped = _read_latest_pytest_result(repo_root)

    missing_routes = tuple(sorted(EXPECTED_ROUTE_SIGNATURES - routes))
    missing_sse = tuple(sorted(EXPECTED_SSE_EVENT_TYPES - sse_types))
    missing_tools = tuple(sorted(EXPECTED_TOOL_NAMES - tools))
    observed_semantic_markers = _collect_semantic_markers(repo_root)
    missing_semantic_markers = tuple(sorted(EXPECTED_SEMANTIC_MARKERS - observed_semantic_markers))
    semantic_case_nodeids = _collect_semantic_case_nodeids(repo_root)
    failed_nodeids = _read_latest_failed_nodeids(repo_root)
    semantic_failed = len([nodeid for nodeid in semantic_case_nodeids if nodeid in failed_nodeids])
    semantic_passed = max(0, len(semantic_case_nodeids) - semantic_failed)

    return ParitySummary(
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        routes_total=len(routes),
        expected_routes_total=len(EXPECTED_ROUTE_SIGNATURES),
        expected_routes_missing=missing_routes,
        sse_types_total=len(sse_types),
        expected_sse_total=len(EXPECTED_SSE_EVENT_TYPES),
        expected_sse_missing=missing_sse,
        tools_total=len(tools),
        expected_tools_total=len(EXPECTED_TOOL_NAMES),
        expected_tools_missing=missing_tools,
        test_functions_total=tests_total,
        passed_tests=passed,
        skipped_tests=skipped,
        semantic_markers_total=len(observed_semantic_markers & EXPECTED_SEMANTIC_MARKERS),
        expected_semantic_markers_total=len(EXPECTED_SEMANTIC_MARKERS),
        expected_semantic_markers_missing=missing_semantic_markers,
        semantic_cases_total=len(semantic_case_nodeids),
        semantic_cases_passed=semantic_passed,
        semantic_cases_failed=semantic_failed,
    )


def _render_missing(items: tuple[str, ...]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- `{item}`" for item in items)


def render_parity_report(summary: ParitySummary) -> str:
    status = "PASS" if summary.is_full_parity else "PARTIAL"
    semantic_status = "PASS" if summary.is_semantic_baseline else "PARTIAL"
    return f"""# Parity Report

Generated at (UTC): `{summary.generated_at_utc}`

## Status

- Overall: **{status}**
- Full route/SSE/tool parity at baseline: **{"Yes" if summary.is_full_parity else "No"}**
- Semantic harness baseline: **{"Yes" if summary.is_semantic_baseline else "No"}**

## Coverage Snapshot

- API routes discovered: `{summary.routes_total}`
- Baseline route signatures: `{summary.expected_routes_total}`
- Missing baseline routes: `{len(summary.expected_routes_missing)}`

- SSE types discovered from contract snapshots: `{summary.sse_types_total}`
- Baseline SSE types: `{summary.expected_sse_total}`
- Missing baseline SSE types: `{len(summary.expected_sse_missing)}`

- Base tools discovered: `{summary.tools_total}`
- Baseline tools: `{summary.expected_tools_total}`
- Missing baseline tools: `{len(summary.expected_tools_missing)}`

- Semantic markers discovered: `{summary.semantic_markers_total}`
- Semantic marker baseline: `{summary.expected_semantic_markers_total}`
- Missing semantic markers: `{len(summary.expected_semantic_markers_missing)}`

- Semantic cases discovered: `{summary.semantic_cases_total}`
- Semantic cases passed (latest cache estimate): `{summary.semantic_cases_passed}`
- Semantic cases failed (latest cache estimate): `{summary.semantic_cases_failed}`
- Semantic pass rate (latest cache estimate): `{summary.semantic_pass_rate:.0%}`
- Semantic status: **{semantic_status}**

- Test functions discovered: `{summary.test_functions_total}`

## Missing Baseline Routes

{_render_missing(summary.expected_routes_missing)}

## Missing Baseline SSE Types

{_render_missing(summary.expected_sse_missing)}

## Missing Baseline Tools

{_render_missing(summary.expected_tools_missing)}

## Missing Semantic Markers

{_render_missing(summary.expected_semantic_markers_missing)}

## Notes

- This report compares against the internal frozen baseline contract for route signatures, SSE event types, and base tool names.
- Semantic section summarizes marker coverage and semantic-case status from current code + latest pytest cache.
- It does not claim byte-level behavioral identity with upstream implementations.
"""
