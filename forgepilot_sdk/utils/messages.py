from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from forgepilot_sdk.types import ConversationMessage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_user_message(
    content: str | list[dict[str, Any]],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = options or {}
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "uuid": str(payload.get("uuid") or uuid.uuid4()),
        "timestamp": _utc_now_iso(),
    }


def create_assistant_message(
    content: list[dict[str, Any]],
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    message = {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
        "uuid": str(uuid.uuid4()),
        "timestamp": _utc_now_iso(),
    }
    if usage is not None:
        message["usage"] = usage
    return message


def _to_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [x for x in content if isinstance(x, dict)]
    if isinstance(content, dict):
        return [content]
    return [{"type": "text", "text": str(content)}]


def normalize_messages_for_api(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    normalized: list[ConversationMessage] = []
    for message in messages:
        if normalized and normalized[-1].role == message.role and message.role == "user":
            merged_blocks = _to_blocks(normalized[-1].content) + _to_blocks(message.content)
            normalized[-1] = ConversationMessage(role="user", content=merged_blocks)
            continue
        normalized.append(ConversationMessage(role=message.role, content=message.content))

    return _fix_tool_result_pairing(normalized)


def _fix_tool_result_pairing(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    fixed: list[ConversationMessage] = []
    for message in messages:
        if message.role == "user" and isinstance(message.content, list) and fixed:
            tool_results = [
                b
                for b in message.content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            if tool_results and fixed[-1].role == "assistant" and isinstance(fixed[-1].content, list):
                tool_use_ids = {
                    str(b.get("id"))
                    for b in fixed[-1].content
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
                }
                valid = []
                for block in message.content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        if str(block.get("tool_use_id")) in tool_use_ids:
                            valid.append(block)
                    else:
                        valid.append(block)
                if valid:
                    fixed.append(ConversationMessage(role=message.role, content=valid))
                continue
        fixed.append(message)
    return fixed


def strip_images_from_messages(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    output: list[ConversationMessage] = []
    for message in messages:
        if not isinstance(message.content, list):
            output.append(message)
            continue
        filtered = [b for b in message.content if not (isinstance(b, dict) and b.get("type") == "image")]
        output.append(
            ConversationMessage(
                role=message.role,
                content=filtered if filtered else "[content removed]",
            )
        )
    return output


def extract_text_from_content(content: list[dict[str, Any]] | str) -> str:
    if isinstance(content, str):
        return content
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(str(block.get("text")))
    return "".join(texts)


def create_compact_boundary_message() -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content="[Previous context has been summarized above. Continuing conversation.]",
    )


def truncate_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    half = max(1, max_length // 2)
    return text[:half] + "\n...(truncated)...\n" + text[-half:]


def createUserMessage(content: str | list[dict[str, Any]], options: dict[str, Any] | None = None) -> dict[str, Any]:
    return create_user_message(content, options)


def createAssistantMessage(content: list[dict[str, Any]], usage: dict[str, int] | None = None) -> dict[str, Any]:
    return create_assistant_message(content, usage)


def normalizeMessagesForAPI(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    return normalize_messages_for_api(messages)


def stripImagesFromMessages(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    return strip_images_from_messages(messages)


def extractTextFromContent(content: list[dict[str, Any]] | str) -> str:
    return extract_text_from_content(content)


def createCompactBoundaryMessage() -> ConversationMessage:
    return create_compact_boundary_message()


def truncateText(text: str, maxLength: int) -> str:
    return truncate_text(text, maxLength)
