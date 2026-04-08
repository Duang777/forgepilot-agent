from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import forgepilot_api.services.agent_service as agent_service
from forgepilot_api.main import app


def test_permission_route_approves_pending_permission() -> None:
    async def _run() -> None:
        session = await agent_service.create_session_async("execute")
        try:
            permission = {"id": "perm-route-approve", "toolName": "Write", "input": {"file_path": "a.txt"}}
            await agent_service._register_permission_request(session.id, permission)

            client = TestClient(app)
            resp = client.post(
                "/agent/permission",
                json={"sessionId": session.id, "permissionId": "perm-route-approve", "approved": True},
            )
            assert resp.status_code == 200
            assert resp.json() == {"success": True, "message": "Permission response received"}

            decision = await agent_service._wait_for_permission_decision(session.id, "perm-route-approve")
            assert decision is True
        finally:
            await agent_service.delete_session_async(session.id)

    asyncio.run(_run())


def test_permission_route_denies_pending_permission() -> None:
    async def _run() -> None:
        session = await agent_service.create_session_async("execute")
        try:
            permission = {"id": "perm-route-deny", "toolName": "Edit", "input": {"file_path": "b.txt"}}
            await agent_service._register_permission_request(session.id, permission)

            client = TestClient(app)
            resp = client.post(
                "/agent/permission",
                json={"sessionId": session.id, "permissionId": "perm-route-deny", "approved": False},
            )
            assert resp.status_code == 200
            assert resp.json() == {"success": True, "message": "Permission response received"}

            decision = await agent_service._wait_for_permission_decision(session.id, "perm-route-deny")
            assert decision is False
        finally:
            await agent_service.delete_session_async(session.id)

    asyncio.run(_run())


def test_permission_route_returns_false_when_no_pending() -> None:
    client = TestClient(app)
    resp = client.post(
        "/agent/permission",
        json={"sessionId": "missing-session", "permissionId": "missing-perm", "approved": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": False, "message": "No pending permission found"}

