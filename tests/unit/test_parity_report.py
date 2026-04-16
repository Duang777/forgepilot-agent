from __future__ import annotations

from pathlib import Path

from forgepilot_api.ops.parity import build_parity_summary, render_parity_report


def test_parity_summary_has_no_missing_baseline_items() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    summary = build_parity_summary(repo_root)
    assert summary.expected_routes_missing == ()
    assert summary.expected_sse_missing == ()
    assert summary.expected_tools_missing == ()
    assert summary.is_full_parity is True


def test_parity_report_render_contains_status_and_counts() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    summary = build_parity_summary(repo_root)
    report = render_parity_report(summary)
    assert "# Parity Report" in report
    assert "Full route/SSE/tool parity at baseline: **Yes**" in report
    assert f"API routes discovered: `{summary.routes_total}`" in report
