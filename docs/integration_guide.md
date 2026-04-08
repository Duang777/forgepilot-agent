# Integration Guide (React/Tauri -> Python API)

This guide maps existing desktop/frontend behavior to `forgepilot_api` and adds deployment-safe controls for ForgePilot Agent.

## Development Mode

Run Python API on port `2026`:

```bash
uvicorn forgepilot_api.app:app --host 127.0.0.1 --port 2026 --reload
```

Frontend defaults to `http://localhost:2026` in dev.

You can explicitly override API base URL:

```bash
VITE_API_BASE_URL=http://127.0.0.1:2026
```

Run desktop dev launcher with runtime smoke check:

```powershell
.\scripts\dev.ps1 -Task desktop
```

Standalone smoke command:

```bash
python scripts/smoke_api_chain.py --base-url http://127.0.0.1:2026 --require-model --require-plan
```

## Production Mode

Default sidecar port remains `2620`.

Run Python service directly if you do not bundle Node sidecar:

```bash
python -m forgepilot_api.sidecar_entry
```

### Build Windows package with Python sidecar

From the resolved frontend shell directory:

```bash
$frontend="$(python scripts/resolve_frontend_shell.py --repo-root . --relative)"
cd "$frontend"
pnpm tauri:build:python:windows
```

This command will:

1. Build a Python one-file sidecar executable via `scripts/build_python_sidecar.py`
2. Place it under `<frontend-shell>/src-api/dist/forgepilot-agent-api-x86_64-pc-windows-msvc.exe`
3. Build Tauri with sidecar config overlay (`src-tauri/tauri.conf.python-sidecar.json`)

Prerequisite:

- `PyInstaller` available in your Python environment
  - install manually: `python -m pip install pyinstaller`
  - or use script flag: `--install-pyinstaller`

One-command release from repo root:

```powershell
.\scripts\release_windows.ps1
```

Cross-platform sidecar build commands:

```bash
$frontend="$(python scripts/resolve_frontend_shell.py --repo-root . --relative)"
cd "$frontend"
pnpm build:api:binary:python:linux
pnpm build:api:binary:python:mac-intel
pnpm build:api:binary:python:mac-arm
```

And sidecar-aware Tauri package commands:

```bash
pnpm tauri:build:python:linux
pnpm tauri:build:python:mac-intel
pnpm tauri:build:python:mac-arm
```

### Tauri sidecar behavior controls

In release builds, Tauri now supports these env controls:

- `FORGEPILOT_DISABLE_SIDECAR=1`
  - Skip bundled sidecar startup.
- `FORGEPILOT_EXTERNAL_API_URL=http://host:port`
  - Also skips bundled sidecar startup (use external API).
- `FORGEPILOT_SIDECAR_NAME=<name>`
  - Override sidecar executable name.
  - Default lookup uses `forgepilot-agent-api`.

If you skip sidecar, build frontend with matching API URL using `VITE_API_BASE_URL`.

### Checksum helper

You can generate sidecar artifact checksums directly:

```bash
python scripts/write_sidecar_checksums.py --repo-root . --pattern "forgepilot-agent-api-*" --output .build/sidecar-sha256.txt
```

### API chain smoke test

Validate planning/execution/SSE flow end-to-end in one test:

```bash
python -m pytest -q tests/e2e/test_plan_execute_chain_smoke.py
```

### Local verification gate

Run all local quality checks before packaging:

```powershell
.\scripts\verify_local.ps1
```

## Endpoint Compatibility Covered

- `GET /health` and dependency compatibility routes
- `GET /metrics` Prometheus text metrics
- `POST /agent`, `/agent/plan`, `/agent/execute`, `/agent/chat`, `/agent/title`
- `POST /agent/stop/{sessionId}` and `GET /agent/session/{sessionId}`
- `GET /audit/logs` (filterable persisted audit trail)
- `GET/POST /mcp/config`, `GET /mcp/path`, `GET /mcp/all-configs`
- `POST /files/readdir`, `/files/stat`, `/files/read`, `/files/read-binary`, `/files/open`, `/files/open-in-editor`
- `GET /providers/sandbox`, `/providers/agents`, switches, config sync, detect
- `GET /preview/node-available`, `POST /preview/start`, `POST /preview/stop`, `GET /preview/status/{taskId}`, `POST /preview/stop-all`

## Data Path Contract

- App dir: `~/.forgepilot`
- DB: `~/.forgepilot/forgepilot.db`
- Sessions: `~/.forgepilot/sessions`
- MCP config: `~/.forgepilot/mcp.json`
- Skills: `~/.forgepilot/skills` and `~/.claude/skills`

## Runtime Environment Controls

- `FORGEPILOT_CORS_ORIGINS`: API CORS origins (`*` or comma-separated values).
- `FORGEPILOT_CORS_ALLOW_CREDENTIALS`: include credentials in CORS responses.
- `FORGEPILOT_LOG_LEVEL`: `DEBUG/INFO/WARNING/ERROR`.
- `FORGEPILOT_REQUEST_ID_HEADER`: request-id header key for tracing.
- `FORGEPILOT_EXPOSE_METRICS`: enable or disable `/metrics`.
- `FORGEPILOT_AUTH_MODE`: `off` or `api_key`.
- `FORGEPILOT_API_KEYS`: comma-separated keys (`subject:key` format recommended).
- `FORGEPILOT_API_KEY_HEADER`: API key header name.
- `FORGEPILOT_AUTH_EXEMPT_PATHS`: path prefix allowlist bypassing auth and rate limit.
- `FORGEPILOT_RATE_LIMIT_ENABLED`: enable request rate limiting.
- `FORGEPILOT_RATE_LIMIT_REQUESTS`: max requests per rate-limit window.
- `FORGEPILOT_RATE_LIMIT_WINDOW_SECONDS`: window size in seconds.
- `FORGEPILOT_RATE_LIMIT_BACKEND`: `memory` or `redis`.
- `FORGEPILOT_RATE_LIMIT_REDIS_URL`: Redis DSN for shared rate limiting.
- `FORGEPILOT_RATE_LIMIT_REDIS_KEY_PREFIX`: Redis key namespace for rate-limit counters.
- `FORGEPILOT_RATE_LIMIT_FAIL_OPEN`: fallback policy when Redis is unavailable.
- `FORGEPILOT_RATE_LIMIT_TRUST_PROXY`: trust proxied client IP headers.
- `FORGEPILOT_RATE_LIMIT_PROXY_HEADER`: proxy IP header key (default `x-forwarded-for`).
- `FORGEPILOT_AUDIT_LOG_ENABLED`: log mutating API actions for audit trail.

