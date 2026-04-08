from __future__ import annotations

import asyncio
import json

from forgepilot_sdk.tools.core import (
    clear_config,
    clear_mailboxes,
    clear_tasks,
    clear_todos,
    get_todos,
    read_mailbox,
    set_deferred_tools,
    set_mcp_connections,
)
from forgepilot_sdk.tools.registry import assemble_tool_pool, get_all_base_tools
from forgepilot_sdk.types import ToolContext


class _FakeMcpConnection:
    def __init__(self) -> None:
        self.name = "demo"
        self.status = "connected"
        self.tools = [{"name": "x"}]

    async def list_resources(self):
        return [{"name": "orders", "description": "Sales order table", "uri": "db://orders"}]

    async def read_resource(self, uri: str):
        assert uri == "db://orders"
        return {"contents": [{"type": "text", "text": "order_id,total\n1,99.9"}]}


def _tool_map():
    return {tool.name: tool for tool in get_all_base_tools()}


def test_assemble_tool_pool_respects_empty_allowed_tools() -> None:
    tools = assemble_tool_pool(get_all_base_tools(), allowed_tools=[])
    assert tools == []


def test_tool_family_includes_full_baseline_set() -> None:
    names = set(_tool_map().keys())
    required = {
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
        "Bash",
        "WebSearch",
        "WebFetch",
        "Skill",
        "TaskCreate",
        "TaskList",
        "TaskUpdate",
        "TaskGet",
        "TaskStop",
        "TaskOutput",
        "Agent",
        "SendMessage",
        "TeamCreate",
        "TeamDelete",
        "EnterWorktree",
        "ExitWorktree",
        "EnterPlanMode",
        "ExitPlanMode",
        "AskUserQuestion",
        "ToolSearch",
        "ListMcpResources",
        "ReadMcpResource",
        "Config",
        "TodoWrite",
        "LSP",
        "CronCreate",
        "CronDelete",
        "CronList",
        "RemoteTrigger",
        "NotebookEdit",
        "Task",
    }
    assert required.issubset(names)


def test_task_tools_roundtrip(tmp_path) -> None:
    clear_tasks()
    tools = _tool_map()
    ctx = ToolContext(cwd=tmp_path)

    created = asyncio.run(tools["TaskCreate"].call({"subject": "Build parser"}, ctx))
    assert created.is_error is False
    assert "Task created:" in created.content

    listed = asyncio.run(tools["TaskList"].call({}, ctx))
    assert "Build parser" in listed.content

    task_id = listed.content.split("]")[0].lstrip("[")
    updated = asyncio.run(
        tools["TaskUpdate"].call({"id": task_id, "status": "completed", "output": "done"}, ctx)
    )
    assert updated.is_error is False
    assert "completed" in updated.content

    got = asyncio.run(tools["TaskGet"].call({"id": task_id}, ctx))
    payload = json.loads(got.content)
    assert payload["status"] == "completed"
    assert payload["output"] == "done"

    output = asyncio.run(tools["TaskOutput"].call({"id": task_id}, ctx))
    assert output.content == "done"


def test_config_todo_and_plan_tools(tmp_path) -> None:
    clear_config()
    clear_todos()
    tools = _tool_map()
    ctx = ToolContext(cwd=tmp_path)

    enter = asyncio.run(tools["EnterPlanMode"].call({}, ctx))
    assert "Entered plan mode" in enter.content
    exit_result = asyncio.run(tools["ExitPlanMode"].call({"plan": "1. read\n2. write", "approved": True}, ctx))
    assert "Plan mode exited" in exit_result.content

    set_result = asyncio.run(tools["Config"].call({"action": "set", "key": "theme", "value": "light"}, ctx))
    assert "Config set:" in set_result.content
    get_result = asyncio.run(tools["Config"].call({"action": "get", "key": "theme"}, ctx))
    assert get_result.content == "\"light\""

    add_todo = asyncio.run(tools["TodoWrite"].call({"action": "add", "text": "Ship v1"}, ctx))
    assert "Todo added" in add_todo.content
    assert len(get_todos()) == 1
    list_todo = asyncio.run(tools["TodoWrite"].call({"action": "list"}, ctx))
    assert "Ship v1" in list_todo.content


def test_message_tool_tool_search_and_mcp_resource_tools(tmp_path) -> None:
    clear_mailboxes()
    tools = _tool_map()
    ctx = ToolContext(cwd=tmp_path)

    send = asyncio.run(tools["SendMessage"].call({"to": "agent-a", "content": "hello"}, ctx))
    assert send.content == "Message sent to agent-a"
    mailbox = read_mailbox("agent-a")
    assert mailbox and mailbox[0]["content"] == "hello"

    set_deferred_tools(list(tools.values()))
    search = asyncio.run(tools["ToolSearch"].call({"query": "mcp resources", "max_results": 3}, ctx))
    assert "Found" in search.content

    set_mcp_connections([_FakeMcpConnection()])
    listed_resources = asyncio.run(tools["ListMcpResources"].call({}, ctx))
    assert "orders" in listed_resources.content

    read_resource = asyncio.run(
        tools["ReadMcpResource"].call({"server": "demo", "uri": "db://orders"}, ctx)
    )
    assert "order_id,total" in read_resource.content

