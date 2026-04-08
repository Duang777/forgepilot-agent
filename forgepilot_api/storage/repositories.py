from __future__ import annotations

import json
from typing import Any

import aiosqlite

from forgepilot_api.config import DB_PATH
from forgepilot_api.storage.db import execute, fetch_all, fetch_one, init_db


async def create_session(session_id: str, prompt: str) -> dict[str, Any]:
    await execute(
        "INSERT OR REPLACE INTO sessions (id, prompt, task_count, updated_at) VALUES (?, ?, COALESCE((SELECT task_count FROM sessions WHERE id=?),0), datetime('now'))",
        (session_id, prompt, session_id),
    )
    row = await fetch_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    return row or {"id": session_id, "prompt": prompt, "task_count": 0}


async def reserve_next_task_index(session_id: str, prompt: str) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("BEGIN IMMEDIATE")
        cursor = await conn.execute("SELECT task_count FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        if row is None:
            task_count = 1
            await conn.execute(
                """
                INSERT INTO sessions (id, prompt, task_count, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
                """,
                (session_id, prompt, task_count),
            )
        else:
            task_count = int(row["task_count"] or 0) + 1
            await conn.execute(
                "UPDATE sessions SET prompt = ?, task_count = ?, updated_at = datetime('now') WHERE id = ?",
                (prompt, task_count, session_id),
            )
        await conn.commit()
        return task_count


async def get_session(session_id: str) -> dict[str, Any] | None:
    return await fetch_one("SELECT * FROM sessions WHERE id = ?", (session_id,))


async def delete_session(session_id: str) -> int:
    return await execute("DELETE FROM sessions WHERE id = ?", (session_id,))


async def upsert_task(
    task_id: str,
    *,
    session_id: str | None,
    task_index: int,
    prompt: str,
    status: str = "running",
) -> dict[str, Any]:
    await execute(
        """
        INSERT INTO tasks (id, session_id, task_index, prompt, status)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          session_id=excluded.session_id,
          task_index=excluded.task_index,
          prompt=excluded.prompt,
          status=excluded.status,
          updated_at=datetime('now')
        """,
        (task_id, session_id, task_index, prompt, status),
    )
    row = await fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return row or {"id": task_id}


async def update_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    updates = []
    values = []
    for key in ["status", "cost", "duration", "prompt", "favorite"]:
        if key in fields and fields[key] is not None:
            updates.append(f"{key}=?")
            val = fields[key]
            if key == "favorite":
                val = 1 if bool(val) else 0
            values.append(val)
    if not updates:
        return await fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    values.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(updates)}, updated_at=datetime('now') WHERE id=?"
    await execute(sql, tuple(values))
    row = await fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return row


async def get_task(task_id: str) -> dict[str, Any] | None:
    row = await fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if row and "favorite" in row:
        row["favorite"] = bool(row["favorite"])
    return row


async def list_tasks() -> list[dict[str, Any]]:
    rows = await fetch_all("SELECT * FROM tasks ORDER BY created_at DESC")
    for row in rows:
        if "favorite" in row:
            row["favorite"] = bool(row["favorite"])
    return rows


async def list_tasks_by_session(session_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        "SELECT * FROM tasks WHERE session_id=? ORDER BY task_index ASC",
        (session_id,),
    )
    for row in rows:
        if "favorite" in row:
            row["favorite"] = bool(row["favorite"])
    return rows


async def create_message(
    *,
    task_id: str,
    msg_type: str,
    content: str | None = None,
    tool_name: str | None = None,
    tool_input: dict | str | None = None,
    tool_output: str | None = None,
    tool_use_id: str | None = None,
    subtype: str | None = None,
    error_message: str | None = None,
    attachments: list[dict] | None = None,
) -> dict[str, Any]:
    tool_input_text = None
    if isinstance(tool_input, dict):
        tool_input_text = json.dumps(tool_input, ensure_ascii=False)
    elif isinstance(tool_input, str):
        tool_input_text = tool_input
    attachments_text = json.dumps(attachments, ensure_ascii=False) if attachments else None

    await execute(
        """
        INSERT INTO messages (task_id, type, content, tool_name, tool_input, tool_output, tool_use_id, subtype, error_message, attachments)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, msg_type, content, tool_name, tool_input_text, tool_output, tool_use_id, subtype, error_message, attachments_text),
    )
    row = await fetch_one("SELECT * FROM messages WHERE id = last_insert_rowid()")
    if not row:
        rows = await fetch_all("SELECT * FROM messages WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,))
        row = rows[0] if rows else {}
    return row or {}


async def list_messages_by_task(task_id: str) -> list[dict[str, Any]]:
    return await fetch_all("SELECT * FROM messages WHERE task_id=? ORDER BY id ASC", (task_id,))


async def create_file(
    *,
    task_id: str,
    name: str,
    file_type: str,
    path: str,
    preview: str | None = None,
    thumbnail: str | None = None,
) -> dict[str, Any]:
    await execute(
        """
        INSERT INTO files (task_id, name, type, path, preview, thumbnail)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, name, file_type, path, preview, thumbnail),
    )
    rows = await fetch_all("SELECT * FROM files WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,))
    return rows[0] if rows else {}


async def list_files_by_task(task_id: str) -> list[dict[str, Any]]:
    return await fetch_all("SELECT * FROM files WHERE task_id=? ORDER BY created_at DESC", (task_id,))


async def read_settings() -> dict[str, Any]:
    try:
        rows = await fetch_all("SELECT key, value FROM settings")
    except Exception:
        # Ensure schema exists, then retry once.
        await init_db()
        rows = await fetch_all("SELECT key, value FROM settings")
    data: dict[str, Any] = {}
    for row in rows:
        key = row["key"]
        try:
            data[key] = json.loads(row["value"])
        except Exception:
            data[key] = row["value"]
    return data


async def write_setting(key: str, value: Any) -> None:
    value_text = json.dumps(value, ensure_ascii=False)
    try:
        await execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')
            """,
            (key, value_text),
        )
    except Exception:
        await init_db()
        await execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')
            """,
            (key, value_text),
        )


async def create_audit_log(
    *,
    request_id: str | None,
    actor: str,
    auth_scheme: str | None,
    method: str,
    path: str,
    status_code: int,
    client_ip: str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata_text = json.dumps(metadata, ensure_ascii=False) if metadata else None
    await execute(
        """
        INSERT INTO audit_logs (request_id, actor, auth_scheme, method, path, status_code, client_ip, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (request_id, actor, auth_scheme, method, path, status_code, client_ip, metadata_text),
    )
    rows = await fetch_all("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else {}


async def list_audit_logs(
    *,
    limit: int = 50,
    offset: int = 0,
    actor: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    if actor:
        where.append("actor = ?")
        params.append(actor)
    if method:
        where.append("method = ?")
        params.append(method.upper())
    if path:
        where.append("path LIKE ?")
        params.append(f"{path}%")
    if status_code is not None:
        where.append("status_code = ?")
        params.append(int(status_code))

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total_row = await fetch_one(f"SELECT COUNT(1) AS c FROM audit_logs {where_sql}", tuple(params))
    total = int(total_row["c"]) if total_row and "c" in total_row else 0

    query_params = [*params, max(1, int(limit)), max(0, int(offset))]
    rows = await fetch_all(
        f"""
        SELECT *
        FROM audit_logs
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(query_params),
    )
    for row in rows:
        raw = row.get("metadata")
        if raw:
            try:
                row["metadata"] = json.loads(raw)
            except Exception:
                pass
    return {"items": rows, "total": total}


def _sqlite_ttl_modifier(ttl_seconds: int | None) -> str | None:
    if ttl_seconds is None:
        return None
    ttl = max(1, int(ttl_seconds))
    return f"+{ttl} seconds"


async def create_runtime_session(session_id: str, phase: str) -> dict[str, Any]:
    await execute(
        """
        INSERT INTO runtime_sessions (id, phase, aborted, created_at, updated_at)
        VALUES (?, ?, 0, datetime('now'), datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
          phase=excluded.phase,
          aborted=0,
          updated_at=datetime('now')
        """,
        (session_id, phase),
    )
    row = await fetch_one("SELECT * FROM runtime_sessions WHERE id = ?", (session_id,))
    return row or {"id": session_id, "phase": phase, "aborted": 0}


async def get_runtime_session(session_id: str) -> dict[str, Any] | None:
    return await fetch_one("SELECT * FROM runtime_sessions WHERE id = ?", (session_id,))


async def set_runtime_session_aborted(session_id: str, aborted: bool = True) -> bool:
    changed = await execute(
        "UPDATE runtime_sessions SET aborted=?, updated_at=datetime('now') WHERE id=?",
        (1 if aborted else 0, session_id),
    )
    return changed > 0


async def delete_runtime_session(session_id: str) -> bool:
    changed = await execute("DELETE FROM runtime_sessions WHERE id = ?", (session_id,))
    return changed > 0


async def save_runtime_plan(plan: dict[str, Any], ttl_seconds: int | None = None) -> dict[str, Any]:
    plan_id = str(plan.get("id") or "")
    if not plan_id:
        raise ValueError("plan.id is required")
    payload_text = json.dumps(plan, ensure_ascii=False)
    modifier = _sqlite_ttl_modifier(ttl_seconds)
    if modifier is None:
        await execute(
            """
            INSERT INTO runtime_plans (id, payload, created_at, expires_at)
            VALUES (?, ?, datetime('now'), NULL)
            ON CONFLICT(id) DO UPDATE SET
              payload=excluded.payload,
              created_at=datetime('now'),
              expires_at=NULL
            """,
            (plan_id, payload_text),
        )
    else:
        await execute(
            """
            INSERT INTO runtime_plans (id, payload, created_at, expires_at)
            VALUES (?, ?, datetime('now'), datetime('now', ?))
            ON CONFLICT(id) DO UPDATE SET
              payload=excluded.payload,
              created_at=datetime('now'),
              expires_at=datetime('now', ?)
            """,
            (plan_id, payload_text, modifier, modifier),
        )
    row = await fetch_one("SELECT * FROM runtime_plans WHERE id = ?", (plan_id,))
    return row or {"id": plan_id}


async def get_runtime_plan(plan_id: str) -> dict[str, Any] | None:
    row = await fetch_one(
        """
        SELECT * FROM runtime_plans
        WHERE id = ?
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        """,
        (plan_id,),
    )
    if not row:
        return None
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            row["payload"] = json.loads(payload)
        except Exception:
            pass
    return row


async def delete_runtime_plan(plan_id: str) -> bool:
    changed = await execute("DELETE FROM runtime_plans WHERE id = ?", (plan_id,))
    return changed > 0


async def delete_expired_runtime_plans() -> int:
    return await execute("DELETE FROM runtime_plans WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')")


async def register_runtime_permission(
    *,
    session_id: str,
    permission_id: str,
    payload: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    payload_text = json.dumps(payload or {}, ensure_ascii=False)
    modifier = _sqlite_ttl_modifier(ttl_seconds)
    if modifier is None:
        await execute(
            """
            INSERT INTO runtime_permissions (session_id, permission_id, payload, status, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, 'pending', datetime('now'), datetime('now'), NULL)
            ON CONFLICT(session_id, permission_id) DO UPDATE SET
              payload=excluded.payload,
              status='pending',
              updated_at=datetime('now'),
              expires_at=NULL
            """,
            (session_id, permission_id, payload_text),
        )
    else:
        await execute(
            """
            INSERT INTO runtime_permissions (session_id, permission_id, payload, status, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, 'pending', datetime('now'), datetime('now'), datetime('now', ?))
            ON CONFLICT(session_id, permission_id) DO UPDATE SET
              payload=excluded.payload,
              status='pending',
              updated_at=datetime('now'),
              expires_at=datetime('now', ?)
            """,
            (session_id, permission_id, payload_text, modifier, modifier),
        )
    row = await fetch_one(
        "SELECT * FROM runtime_permissions WHERE session_id=? AND permission_id=?",
        (session_id, permission_id),
    )
    return row or {"session_id": session_id, "permission_id": permission_id, "status": "pending"}


async def get_runtime_permission(session_id: str, permission_id: str) -> dict[str, Any] | None:
    row = await fetch_one(
        """
        SELECT * FROM runtime_permissions
        WHERE session_id = ?
          AND permission_id = ?
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        """,
        (session_id, permission_id),
    )
    if not row:
        return None
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            row["payload"] = json.loads(payload)
        except Exception:
            pass
    return row


async def set_runtime_permission_status(session_id: str, permission_id: str, status: str) -> bool:
    changed = await execute(
        """
        UPDATE runtime_permissions
        SET status=?, updated_at=datetime('now')
        WHERE session_id=? AND permission_id=?
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        """,
        (status, session_id, permission_id),
    )
    return changed > 0


async def delete_runtime_permission(session_id: str, permission_id: str) -> bool:
    changed = await execute(
        "DELETE FROM runtime_permissions WHERE session_id=? AND permission_id=?",
        (session_id, permission_id),
    )
    return changed > 0


async def delete_expired_runtime_permissions() -> int:
    return await execute(
        "DELETE FROM runtime_permissions WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"
    )

