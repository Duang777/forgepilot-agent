from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DecisionAction = Literal["allow", "deny", "require_permission"]
RiskLevel = Literal["low", "medium", "high"]

_FILE_SCOPED_TOOLS = {"read", "write", "edit", "notebookedit"}
_BASH_TOOL_NAMES = {"bash"}
_HIGH_RISK_PATTERNS = (
    re.compile(r"(^|\s)sudo(\s|$)", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|\:\&\s*;\s*\}", re.IGNORECASE),
)
_MEDIUM_RISK_PATTERNS = (
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\b", re.IGNORECASE),
    re.compile(r"\bdocker\b", re.IGNORECASE),
    re.compile(r"\bmv\b", re.IGNORECASE),
    re.compile(r"\bcp\b", re.IGNORECASE),
    re.compile(r"\bmkdir\b", re.IGNORECASE),
    re.compile(r"\btouch\b", re.IGNORECASE),
    re.compile(r"\bchmod\b", re.IGNORECASE),
)
_REDIRECT_PATH_RE = re.compile(r"(?:^|[;\s])(?:>|>>|<|2>|2>>)\s*([^\s;&|]+)")
_PATH_TOKEN_RE = re.compile(r"[/\\]|^\.\.?$|^\./|^\.\./|^~[/\\]")
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


@dataclass(slots=True)
class PolicyDecision:
    action: DecisionAction
    risk_level: RiskLevel
    reason: str
    normalized_input: dict[str, Any]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    return default


def _is_production_mode() -> bool:
    node_env = os.getenv("NODE_ENV", "").strip().lower()
    files_mode = os.getenv("FORGEPILOT_FILES_MODE", "").strip().lower()
    return node_env == "production" or files_mode in {"prod", "production"}


def _policy_enabled() -> bool:
    return _env_bool("FORGEPILOT_POLICY_ENABLED", True)


def _strict_prod() -> bool:
    return _env_bool("FORGEPILOT_POLICY_STRICT_PROD", True)


def _dev_relaxed() -> bool:
    return _env_bool("FORGEPILOT_POLICY_DEV_RELAXED", True)


def _bash_high_risk_mode() -> str:
    mode = os.getenv("FORGEPILOT_BASH_HIGH_RISK_MODE", "deny").strip().lower()
    if mode not in {"deny", "require_permission", "allow"}:
        return "deny"
    return mode


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_scoped_path(cwd: Path, raw_path: str) -> tuple[Path | None, str | None]:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = cwd / path

    # Resolve existing symlinks in ancestor chain while allowing non-existing leaf path.
    normalized = path.resolve(strict=False)
    root = cwd.resolve(strict=False)
    if not _is_within(root, normalized):
        return None, f"Path escapes workspace boundary: {raw_path}"
    return normalized, None


def _extract_command_path_tokens(command: str) -> list[str]:
    tokens: list[str] = []
    try:
        parsed = shlex.split(command, posix=True)
    except Exception:
        parsed = command.split()

    for token in parsed:
        stripped = token.strip()
        if not stripped:
            continue
        if "://" in stripped:
            continue
        if _PATH_TOKEN_RE.search(stripped):
            tokens.append(stripped)

    for match in _REDIRECT_PATH_RE.finditer(command):
        candidate = match.group(1).strip()
        if candidate and "://" not in candidate:
            tokens.append(candidate)
    return tokens


def _classify_bash_risk(command: str) -> RiskLevel:
    compact = command.strip()
    if not compact:
        return "low"
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(compact):
            return "high"
    for pattern in _MEDIUM_RISK_PATTERNS:
        if pattern.search(compact):
            return "medium"
    return "low"


def evaluate_tool_policy(tool_name: str, input_data: dict[str, Any], cwd: Path) -> PolicyDecision:
    normalized_input = dict(input_data)
    normalized_tool = str(tool_name or "").strip().lower()

    if not _policy_enabled():
        return PolicyDecision(
            action="allow",
            risk_level="low",
            reason="Policy disabled",
            normalized_input=normalized_input,
        )

    enforce_prod = _strict_prod() and _is_production_mode()

    if normalized_tool in _FILE_SCOPED_TOOLS:
        raw = str(input_data.get("file_path") or input_data.get("path") or "").strip()
        if not raw:
            return PolicyDecision(
                action="deny",
                risk_level="high",
                reason="file_path/path is required for file-scoped tool",
                normalized_input=normalized_input,
            )
        resolved, error = _normalize_scoped_path(cwd, raw)
        if error:
            return PolicyDecision(
                action="deny",
                risk_level="high",
                reason=error,
                normalized_input=normalized_input,
            )
        normalized_input["file_path"] = str(resolved)
        if "path" in normalized_input:
            normalized_input["path"] = str(resolved)
        return PolicyDecision(
            action="allow",
            risk_level="low",
            reason="Path is within workspace boundary",
            normalized_input=normalized_input,
        )

    if normalized_tool in _BASH_TOOL_NAMES:
        command = str(input_data.get("command") or "").strip()
        if not command:
            return PolicyDecision(
                action="deny",
                risk_level="high",
                reason="command is required for Bash tool",
                normalized_input=normalized_input,
            )

        for token in _extract_command_path_tokens(command):
            resolved, error = _normalize_scoped_path(cwd, token)
            if error:
                return PolicyDecision(
                    action="deny",
                    risk_level="high",
                    reason=f"Command references out-of-workspace path: {token}",
                    normalized_input=normalized_input,
                )
            normalized_input.setdefault("_normalized_paths", []).append(str(resolved))

        risk = _classify_bash_risk(command)
        if risk == "high":
            mode = _bash_high_risk_mode()
            if mode == "allow" and not enforce_prod and _dev_relaxed():
                return PolicyDecision(
                    action="allow",
                    risk_level="high",
                    reason="High-risk command allowed by dev relaxation",
                    normalized_input=normalized_input,
                )
            if mode == "require_permission":
                return PolicyDecision(
                    action="require_permission",
                    risk_level="high",
                    reason="High-risk command requires explicit approval",
                    normalized_input=normalized_input,
                )
            return PolicyDecision(
                action="deny",
                risk_level="high",
                reason="High-risk command denied by policy",
                normalized_input=normalized_input,
            )

        if risk == "medium":
            if not enforce_prod and _dev_relaxed():
                return PolicyDecision(
                    action="allow",
                    risk_level="medium",
                    reason="Medium-risk command allowed in relaxed dev mode",
                    normalized_input=normalized_input,
                )
            return PolicyDecision(
                action="require_permission",
                risk_level="medium",
                reason="Medium-risk command requires explicit approval",
                normalized_input=normalized_input,
            )

        return PolicyDecision(
            action="allow",
            risk_level="low",
            reason="Low-risk command",
            normalized_input=normalized_input,
        )

    return PolicyDecision(
        action="allow",
        risk_level="low",
        reason="No policy rule for tool",
        normalized_input=normalized_input,
    )
