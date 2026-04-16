from __future__ import annotations

import asyncio

import forgepilot_api.api.agent as agent_api


def test_extract_paths_from_tool_context_for_write_output() -> None:
    paths = agent_api._extract_paths_from_tool_context(
        "Write",
        {"file_path": "index.html"},
        r"File written: C:\Users\13087\.forgepilot\sessions\abc\index.html (24 lines, 512 bytes)",
    )
    assert paths == [r"C:\Users\13087\.forgepilot\sessions\abc\index.html"]


def test_extract_paths_from_tool_context_for_bash_output() -> None:
    paths = agent_api._extract_paths_from_tool_context(
        "Bash",
        {"command": "echo done"},
        r"Generated file at C:\Users\13087\.forgepilot\sessions\abc\report.md",
    )
    assert r"C:\Users\13087\.forgepilot\sessions\abc\report.md" in paths


def test_persist_tool_result_records_file_artifact(monkeypatch) -> None:
    recorded_upserts: list[dict] = []

    async def _fake_create_message(**kwargs):
        return kwargs

    async def _fake_upsert_file_by_path(**kwargs):
        recorded_upserts.append(kwargs)
        return kwargs

    monkeypatch.setattr(agent_api, "repo_create_message", _fake_create_message)
    monkeypatch.setattr(agent_api, "repo_upsert_file_by_path", _fake_upsert_file_by_path)

    task_id = "task-artifact-1"

    async def _run() -> None:
        await agent_api._persist_agent_event(
            task_id,
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "Write",
                "input": {"file_path": "index.html", "content": "<html></html>"},
            },
        )
        await agent_api._persist_agent_event(
            task_id,
            {
                "type": "tool_result",
                "toolUseId": "tool-1",
                "output": r"File written: C:\Users\13087\.forgepilot\sessions\abc\index.html (1 lines, 13 bytes)",
                "isError": False,
            },
        )

    asyncio.run(_run())

    assert len(recorded_upserts) == 1
    assert recorded_upserts[0]["task_id"] == task_id
    assert recorded_upserts[0]["path"] == r"C:\Users\13087\.forgepilot\sessions\abc\index.html"
    assert recorded_upserts[0]["name"] == "index.html"
    assert recorded_upserts[0]["file_type"] == "website"

