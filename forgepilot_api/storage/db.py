from __future__ import annotations

import asyncio

import aiosqlite

from forgepilot_api.config import DB_PATH


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY NOT NULL,
  prompt TEXT NOT NULL,
  task_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT,
  task_index INTEGER DEFAULT 1,
  prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  cost REAL,
  duration INTEGER,
  favorite INTEGER DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tasks_session_id ON tasks(session_id);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  type TEXT NOT NULL,
  content TEXT,
  tool_name TEXT,
  tool_input TEXT,
  tool_output TEXT,
  tool_use_id TEXT,
  subtype TEXT,
  error_message TEXT,
  attachments TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_task_id ON messages(task_id);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  path TEXT NOT NULL,
  preview TEXT,
  thumbnail TEXT,
  is_favorite INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_files_task_id ON files(task_id);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY NOT NULL,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT,
  actor TEXT NOT NULL,
  auth_scheme TEXT,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  client_ip TEXT,
  metadata TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor);
CREATE INDEX IF NOT EXISTS idx_audit_logs_path ON audit_logs(path);

CREATE TABLE IF NOT EXISTS runtime_sessions (
  id TEXT PRIMARY KEY NOT NULL,
  phase TEXT NOT NULL,
  aborted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_runtime_sessions_updated ON runtime_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS runtime_plans (
  id TEXT PRIMARY KEY NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runtime_plans_expires ON runtime_plans(expires_at);

CREATE TABLE IF NOT EXISTS runtime_permissions (
  session_id TEXT NOT NULL,
  permission_id TEXT NOT NULL,
  payload TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT,
  PRIMARY KEY(session_id, permission_id),
  FOREIGN KEY(session_id) REFERENCES runtime_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_runtime_permissions_status ON runtime_permissions(status);
CREATE INDEX IF NOT EXISTS idx_runtime_permissions_expires ON runtime_permissions(expires_at);
"""

_db_initialized = False
_db_init_lock = asyncio.Lock()


async def ensure_db_initialized() -> None:
    global _db_initialized
    if _db_initialized:
        return
    async with _db_init_lock:
        if _db_initialized:
            return
        await init_db()


async def init_db() -> None:
    global _db_initialized
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()
    _db_initialized = True


async def execute(sql: str, params: tuple = ()) -> int:
    await ensure_db_initialized()
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(sql, params)
        await conn.commit()
        return cursor.rowcount


async def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    await ensure_db_initialized()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    await ensure_db_initialized()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
