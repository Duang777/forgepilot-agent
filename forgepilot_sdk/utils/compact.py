from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from forgepilot_sdk.providers.base import LLMProvider
from forgepilot_sdk.types import ConversationMessage
from forgepilot_sdk.utils.messages import strip_images_from_messages
from forgepilot_sdk.utils.tokens import estimate_messages_tokens, get_auto_compact_threshold


@dataclass(slots=True)
class AutoCompactState:
    compacted: bool = False
    turn_counter: int = 0
    consecutive_failures: int = 0


def create_auto_compact_state() -> AutoCompactState:
    return AutoCompactState()


def should_auto_compact(
    messages: list[ConversationMessage],
    model: str,
    state: AutoCompactState,
    *,
    threshold_tokens: int | None = None,
) -> bool:
    if state.consecutive_failures >= 3:
        return False
    normalized = [{"role": m.role, "content": m.content} for m in messages]
    estimated = estimate_messages_tokens(normalized)
    threshold = int(threshold_tokens) if threshold_tokens is not None else get_auto_compact_threshold(model)
    return estimated >= threshold


def _build_compaction_prompt(messages: list[ConversationMessage]) -> str:
    parts: list[str] = [
        "Summarize the following conversation chunk into strict JSON with keys:",
        "goal, done, todo, decisions, risks, touched_files.",
        "Rules:",
        "- goal: short string",
        "- done/todo/decisions/risks/touched_files: arrays of short strings",
        "- include only grounded facts from the chunk",
        "- output JSON only",
        "",
        "Conversation chunk:",
    ]
    for message in messages:
        role = "User" if message.role == "user" else "Assistant"
        if isinstance(message.content, str):
            parts.append(f"{role}: {message.content[:5000]}")
            continue
        if isinstance(message.content, list):
            text_lines: list[str] = []
            for block in message.content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_lines.append(str(block.get("text") or "")[:3000])
                elif block.get("type") == "tool_use":
                    text_lines.append(f"[Tool: {block.get('name')}]")
                elif block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, str):
                        text_lines.append(f"[Tool Result: {content[:1200]}]")
                    else:
                        text_lines.append("[Tool Result]")
            if text_lines:
                parts.append(f"{role}: " + "\n".join(text_lines))
    return "\n\n".join(parts)


def _extract_text_from_response_content(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
    return "\n".join(texts)


def _normalize_summary_payload(raw: dict[str, Any]) -> dict[str, Any]:
    goal = str(raw.get("goal") or "").strip()

    def _as_items(key: str) -> list[str]:
        value = raw.get(key)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    return {
        "goal": goal,
        "done": _as_items("done"),
        "todo": _as_items("todo"),
        "decisions": _as_items("decisions"),
        "risks": _as_items("risks"),
        "touched_files": _as_items("touched_files"),
    }


def _extract_structured_summary(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()

    parsed: dict[str, Any] | None = None
    try:
        loaded = json.loads(candidate)
        if isinstance(loaded, dict):
            parsed = loaded
    except Exception:
        parsed = None

    if parsed is None:
        fallback = {
            "goal": candidate[:240],
            "done": ["Generated from freeform summary due to malformed JSON output."],
            "todo": [],
            "decisions": [],
            "risks": [],
            "touched_files": [],
        }
        return _normalize_summary_payload(fallback)

    return _normalize_summary_payload(parsed)


def _render_structured_summary(summary: dict[str, Any]) -> str:
    def _render_list(items: list[str]) -> str:
        if not items:
            return "- (none)"
        return "\n".join(f"- {item}" for item in items)

    return (
        "[Context Summary]\n"
        + f"Goal: {summary.get('goal') or '(unknown)'}\n\n"
        + "Done:\n"
        + _render_list(summary.get("done") or [])
        + "\n\nTodo:\n"
        + _render_list(summary.get("todo") or [])
        + "\n\nDecisions:\n"
        + _render_list(summary.get("decisions") or [])
        + "\n\nRisks:\n"
        + _render_list(summary.get("risks") or [])
        + "\n\nTouched Files:\n"
        + _render_list(summary.get("touched_files") or [])
    )


def _select_compaction_slice(
    messages: list[ConversationMessage],
    *,
    keep_recent_turns: int,
    summarize_earliest_turns: int,
) -> tuple[list[ConversationMessage], list[ConversationMessage]]:
    keep_recent = max(1, int(keep_recent_turns))
    summarize_earliest = max(1, int(summarize_earliest_turns))

    if len(messages) <= keep_recent:
        return [], messages

    compaction_capacity = max(0, len(messages) - keep_recent)
    chunk_size = min(summarize_earliest, compaction_capacity)
    chunk = messages[:chunk_size]
    preserved = messages[chunk_size:]
    return chunk, preserved


async def compact_conversation(
    provider: LLMProvider,
    model: str,
    messages: list[ConversationMessage],
    state: AutoCompactState,
    *,
    keep_recent_turns: int = 8,
    summarize_earliest_turns: int = 30,
    summarizer_model: str | None = None,
) -> dict[str, Any]:
    try:
        stripped = strip_images_from_messages(messages)
        chunk, preserved = _select_compaction_slice(
            stripped,
            keep_recent_turns=keep_recent_turns,
            summarize_earliest_turns=summarize_earliest_turns,
        )

        if not chunk:
            return {
                "compacted_messages": messages,
                "summary": "",
                "summary_structured": {},
                "state": state,
                "compacted_count": 0,
                "preserved_count": len(messages),
            }

        prompt = _build_compaction_prompt(chunk)
        response = await provider.create_message(
            model=summarizer_model or model,
            max_tokens=8192,
            system_prompt=(
                "You are a structured conversation summarizer. "
                "Return strict JSON only, no markdown wrappers."
            ),
            messages=[ConversationMessage(role="user", content=prompt)],
            tools=[],
            thinking=None,
        )
        summary_text_raw = _extract_text_from_response_content(response.content)
        structured = _extract_structured_summary(summary_text_raw)
        summary = _render_structured_summary(structured)

        compacted_messages = [
            ConversationMessage(
                role="user",
                content=(
                    "[Previous conversation summary]\n\n"
                    + summary
                    + "\n\n[End of summary - conversation continues below]"
                ),
            ),
            ConversationMessage(
                role="assistant",
                content="I understand the context from the previous conversation. I'll continue from where we left off.",
            ),
            *preserved,
        ]
        return {
            "compacted_messages": compacted_messages,
            "summary": summary,
            "summary_structured": structured,
            "state": AutoCompactState(
                compacted=True,
                turn_counter=state.turn_counter,
                consecutive_failures=0,
            ),
            "compacted_count": len(chunk),
            "preserved_count": len(preserved),
        }
    except Exception:
        return {
            "compacted_messages": messages,
            "summary": "",
            "summary_structured": {},
            "state": AutoCompactState(
                compacted=state.compacted,
                turn_counter=state.turn_counter,
                consecutive_failures=state.consecutive_failures + 1,
            ),
            "compacted_count": 0,
            "preserved_count": len(messages),
        }


def micro_compact_messages(
    messages: list[ConversationMessage],
    max_tool_result_chars: int = 50_000,
) -> list[ConversationMessage]:
    output: list[ConversationMessage] = []
    for message in messages:
        if not isinstance(message.content, list):
            output.append(message)
            continue
        compacted_blocks: list[Any] = []
        for block in message.content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(block.get("content"), str)
                and len(block["content"]) > max_tool_result_chars
            ):
                content = block["content"]
                half = max_tool_result_chars // 2
                compacted_blocks.append(
                    {
                        **block,
                        "content": content[:half] + "\n...(truncated)...\n" + content[-half:],
                    }
                )
            else:
                compacted_blocks.append(block)
        output.append(ConversationMessage(role=message.role, content=compacted_blocks))
    return output


def createAutoCompactState() -> AutoCompactState:
    return create_auto_compact_state()


def shouldAutoCompact(
    messages: list[ConversationMessage],
    model: str,
    state: AutoCompactState,
) -> bool:
    return should_auto_compact(messages, model, state)


async def compactConversation(
    provider: LLMProvider,
    model: str,
    messages: list[ConversationMessage],
    state: AutoCompactState,
) -> dict[str, Any]:
    return await compact_conversation(provider, model, messages, state)


def microCompactMessages(
    messages: list[ConversationMessage],
    maxToolResultChars: int = 50_000,
) -> list[ConversationMessage]:
    return micro_compact_messages(messages, max_tool_result_chars=maxToolResultChars)
