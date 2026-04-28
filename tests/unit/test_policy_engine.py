from __future__ import annotations

from forgepilot_sdk.policy import evaluate_tool_policy


def test_file_scoped_tool_normalizes_path_within_workspace(tmp_path) -> None:
    decision = evaluate_tool_policy(
        "Write",
        {"file_path": "nested/index.html", "content": "<html></html>"},
        tmp_path,
    )

    assert decision.action == "allow"
    assert decision.risk_level == "low"
    assert decision.normalized_input["file_path"].startswith(str(tmp_path))


def test_file_scoped_tool_denies_path_escape(tmp_path) -> None:
    decision = evaluate_tool_policy("Read", {"file_path": "../secret.txt"}, tmp_path)

    assert decision.action == "deny"
    assert decision.risk_level == "high"
    assert "escapes workspace" in decision.reason.lower()


def test_bash_denies_out_of_workspace_path_reference(tmp_path) -> None:
    decision = evaluate_tool_policy("Bash", {"command": "cat ../secret.txt"}, tmp_path)

    assert decision.action == "deny"
    assert decision.risk_level == "high"
    assert "out-of-workspace" in decision.reason.lower()


def test_bash_medium_risk_requires_permission_when_dev_relaxed_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_POLICY_DEV_RELAXED", "0")

    decision = evaluate_tool_policy("Bash", {"command": "git push origin main"}, tmp_path)

    assert decision.action == "require_permission"
    assert decision.risk_level == "medium"


def test_bash_high_risk_denied_by_default(tmp_path) -> None:
    decision = evaluate_tool_policy("Bash", {"command": "rm -rf ./tmp"}, tmp_path)

    assert decision.action == "deny"
    assert decision.risk_level == "high"


def test_bash_high_risk_can_require_permission(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_BASH_HIGH_RISK_MODE", "require_permission")

    decision = evaluate_tool_policy("Bash", {"command": "rm -rf ./tmp"}, tmp_path)

    assert decision.action == "require_permission"
    assert decision.risk_level == "high"


def test_bash_high_risk_allow_respects_dev_relaxed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FORGEPILOT_BASH_HIGH_RISK_MODE", "allow")
    monkeypatch.setenv("FORGEPILOT_POLICY_DEV_RELAXED", "1")

    decision = evaluate_tool_policy("Bash", {"command": "rm -rf ./tmp"}, tmp_path)

    assert decision.action == "allow"
    assert decision.risk_level == "high"


def test_bash_high_risk_allow_is_blocked_under_strict_prod(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("FORGEPILOT_POLICY_STRICT_PROD", "1")
    monkeypatch.setenv("FORGEPILOT_BASH_HIGH_RISK_MODE", "allow")

    decision = evaluate_tool_policy("Bash", {"command": "rm -rf ./tmp"}, tmp_path)

    assert decision.action == "deny"
    assert decision.risk_level == "high"
