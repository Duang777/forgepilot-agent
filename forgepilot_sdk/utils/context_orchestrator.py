from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from forgepilot_sdk.providers.base import LLMProvider
from forgepilot_sdk.types import ConversationMessage
from forgepilot_sdk.utils.compact import AutoCompactState, compact_conversation, should_auto_compact

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except Exception:
        return default
    return max(minimum, value)


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


@dataclass(slots=True, frozen=True)
class ContextWindowConfig:
    enabled: bool
    threshold_tokens: int | None
    keep_recent_turns: int
    summarize_earliest_turns: int
    summarizer_model: str | None


@dataclass(slots=True)
class ContextOrchestratorState:
    compaction_count: int = 0
    last_summary: str = ""
    last_summary_structured: dict[str, Any] | None = None
    last_compacted_count: int = 0
    last_preserved_count: int = 0
    last_compacted_turn: int = 0
    last_compacted_at: str = ""


class ContextOrchestrator:
    def __init__(self, provider: LLMProvider, default_model: str) -> None:
        threshold_raw = os.getenv("FORGEPILOT_CONTEXT_WINDOW_THRESHOLD", "").strip()
        threshold_tokens = int(threshold_raw) if threshold_raw.isdigit() and int(threshold_raw) > 0 else None
        summarizer_model = os.getenv("FORGEPILOT_CONTEXT_SUMMARY_MODEL", "").strip() or None

        self.provider = provider
        self.default_model = default_model
        self.window_config = ContextWindowConfig(
            enabled=_env_bool("FORGEPILOT_CONTEXT_WINDOW_ENABLED", True),
            threshold_tokens=threshold_tokens,
            keep_recent_turns=_env_int("FORGEPILOT_CONTEXT_KEEP_RECENT_TURNS", 8),
            summarize_earliest_turns=_env_int("FORGEPILOT_CONTEXT_SUMMARIZE_EARLIEST_TURNS", 30),
            summarizer_model=summarizer_model,
        )
        self.state = ContextOrchestratorState()

    async def apply_before_model_call(
        self,
        *,
        messages: list[ConversationMessage],
        active_model: str,
        compact_state: AutoCompactState,
        turn_count: int,
    ) -> dict[str, Any]:
        if not self.window_config.enabled:
            return {
                "messages": messages,
                "compact_state": compact_state,
                "summary": "",
                "summary_structured": {},
            }

        should = should_auto_compact(
            messages,
            active_model,
            compact_state,
            threshold_tokens=self.window_config.threshold_tokens,
        )
        if not should:
            return {
                "messages": messages,
                "compact_state": compact_state,
                "summary": "",
                "summary_structured": {},
            }

        result = await compact_conversation(
            self.provider,
            active_model,
            messages,
            compact_state,
            keep_recent_turns=self.window_config.keep_recent_turns,
            summarize_earliest_turns=self.window_config.summarize_earliest_turns,
            summarizer_model=self.window_config.summarizer_model,
        )

        summary = str(result.get("summary") or "")
        structured = result.get("summary_structured")
        compacted_count = int(result.get("compacted_count") or 0)
        preserved_count = int(result.get("preserved_count") or 0)

        if summary:
            self.state.compaction_count += 1
            self.state.last_summary = summary
            self.state.last_summary_structured = structured if isinstance(structured, dict) else None
            self.state.last_compacted_count = compacted_count
            self.state.last_preserved_count = preserved_count
            self.state.last_compacted_turn = turn_count
            self.state.last_compacted_at = datetime.now(timezone.utc).isoformat()

        next_messages = result.get("compacted_messages")
        next_state = result.get("state")
        return {
            "messages": next_messages if isinstance(next_messages, list) else messages,
            "compact_state": next_state if isinstance(next_state, AutoCompactState) else compact_state,
            "summary": summary,
            "summary_structured": structured if isinstance(structured, dict) else {},
        }

    def export_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "window": {
                "enabled": self.window_config.enabled,
                "thresholdTokens": self.window_config.threshold_tokens,
                "keepRecentTurns": self.window_config.keep_recent_turns,
                "summarizeEarliestTurns": self.window_config.summarize_earliest_turns,
                "summarizerModel": self.window_config.summarizer_model,
            },
            "stats": {
                "compactionCount": self.state.compaction_count,
                "lastCompactedCount": self.state.last_compacted_count,
                "lastPreservedCount": self.state.last_preserved_count,
                "lastCompactedTurn": self.state.last_compacted_turn,
                "lastCompactedAt": self.state.last_compacted_at,
            },
        }
        if self.state.last_summary:
            payload["lastSummary"] = self.state.last_summary
        if self.state.last_summary_structured:
            payload["lastSummaryStructured"] = self.state.last_summary_structured
        return payload
