from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "forgepilot"
APP_DIR_NAME = ".forgepilot"
DEFAULT_API_PORT = 2620
DEV_API_PORT = 2026

API_PORT = int(os.getenv("PORT", str(DEFAULT_API_PORT if os.getenv("NODE_ENV") == "production" else DEV_API_PORT)))
API_HOST = os.getenv("HOST", "127.0.0.1")

HOME = Path.home()
APP_DIR = HOME / APP_DIR_NAME
APP_DIR.mkdir(parents=True, exist_ok=True)

WORK_DIR = Path(
    os.getenv("FORGEPILOT_WORK_DIR", os.getenv("AGENT_WORK_DIR", str(APP_DIR)))
).expanduser().resolve()
WORK_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = APP_DIR / "forgepilot.db"
SESSIONS_DIR = APP_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

MCP_CONFIG_PATH = APP_DIR / "mcp.json"
SKILLS_DIR = APP_DIR / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def get_home_dir() -> Path:
    return HOME


def get_app_skills_dir() -> Path:
    return SKILLS_DIR


def get_claude_skills_dir() -> Path:
    return HOME / ".claude" / "skills"


def get_all_skills_dirs() -> list[dict[str, str]]:
    return [
        {"name": "forgepilot", "path": str(get_app_skills_dir())},
        {"name": "claude", "path": str(get_claude_skills_dir())},
    ]


def get_primary_mcp_config_path() -> Path:
    return MCP_CONFIG_PATH


def get_claude_settings_path() -> Path:
    return HOME / ".claude" / "settings.json"


def get_all_mcp_config_paths() -> list[dict[str, str]]:
    return [
        {"name": "forgepilot", "path": str(get_primary_mcp_config_path())},
        {"name": "claude", "path": str(get_claude_settings_path())},
    ]
