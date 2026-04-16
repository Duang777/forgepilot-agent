from __future__ import annotations

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
) -> bool:
    if state.consecutive_failures >= 3:
        return False
    normalized = [{"role": m.role, "content": m.content} for m in messages]
    estimated = estimate_messages_tokens(normalized)
    return estimated >= get_auto_compact_threshold(model)


def _build_compaction_prompt(messages: list[ConversationMessage]) -> str:
    parts: list[str] = ["Please summarize this conversation:\n"]
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
                        text_lines.append(f"[Tool Result: {content[:1000]}]")
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


async def compact_conversation(
    provider: LLMProvider,
    model: str,
    messages: list[ConversationMessage],
    state: AutoCompactState,
) -> dict[str, Any]:
    try:
        stripped = strip_images_from_messages(messages)
        prompt = _build_compaction_prompt(stripped)
        response = await provider.create_message(
            model=model,
            max_tokens=8192,
            system_prompt=(
                "You are a conversation summarizer. Create a detailed summary preserving context, "
                "decisions, files modified, tool outputs, and current state."
            ),
            messages=[ConversationMessage(role="user", content=prompt)],
            tools=[],
            thinking=None,
        )
        summary = _extract_text_from_response_content(response.content)
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
        ]
        return {
            "compacted_messages": compacted_messages,
            "summary": summary,
            "state": AutoCompactState(
                compacted=True,
                turn_counter=state.turn_counter,
                consecutive_failures=0,
            ),
        }
    except Exception:
        return {
            "compacted_messages": messages,
            "summary": "",
            "state": AutoCompactState(
                compacted=state.compacted,
                turn_counter=state.turn_counter,
                consecutive_failures=state.consecutive_failures + 1,
            ),
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
