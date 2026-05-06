from __future__ import annotations

import asyncio

from forgepilot_sdk.types import ConversationMessage
from forgepilot_sdk.utils.compact import create_auto_compact_state
from forgepilot_sdk.utils.context_orchestrator import ContextOrchestrator


class _Provider:
    api_type = "openai-completions"

    async def create_message(self, **kwargs):  # type: ignore[override]
        del kwargs
        return type(
            "Resp",
            (),
            {
                "content": '{"goal":"Build landing page","done":["Scaffold created"],"todo":["Add hero"],"decisions":["Use semantic HTML"],"risks":["None"],"touched_files":["index.html"]}',
                "tool_calls": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )()


def _run(coro):
    return asyncio.run(coro)


def test_context_orchestrator_compacts_earliest_chunk_and_keeps_recent(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_CONTEXT_WINDOW_THRESHOLD", "1")
    monkeypatch.setenv("FORGEPILOT_CONTEXT_KEEP_RECENT_TURNS", "2")
    monkeypatch.setenv("FORGEPILOT_CONTEXT_SUMMARIZE_EARLIEST_TURNS", "3")

    messages = [ConversationMessage(role="user", content=f"m-{idx}") for idx in range(1, 8)]
    orchestrator = ContextOrchestrator(_Provider(), "gpt-4o")
    state = create_auto_compact_state()

    result = _run(
        orchestrator.apply_before_model_call(
            messages=messages,
            active_model="gpt-4o",
            compact_state=state,
            turn_count=4,
        )
    )

    compacted = result["messages"]
    assert isinstance(compacted, list)
    # summary pair + preserved tail
    assert len(compacted) == 2 + (len(messages) - 3)
    assert "[Context Summary]" in str(compacted[0].content)
    assert str(compacted[-1].content) == "m-7"

    metadata = orchestrator.export_metadata()
    assert metadata["stats"]["compactionCount"] == 1
    assert metadata["stats"]["lastCompactedCount"] == 3
    assert metadata["stats"]["lastCompactedTurn"] == 4


def test_context_orchestrator_no_compaction_when_under_threshold(monkeypatch) -> None:
    monkeypatch.setenv("FORGEPILOT_CONTEXT_WINDOW_THRESHOLD", "999999")
    messages = [ConversationMessage(role="user", content="short")]
    orchestrator = ContextOrchestrator(_Provider(), "gpt-4o")

    result = _run(
        orchestrator.apply_before_model_call(
            messages=messages,
            active_model="gpt-4o",
            compact_state=create_auto_compact_state(),
            turn_count=1,
        )
    )
    assert result["messages"] == messages
    assert result["summary"] == ""
