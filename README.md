# ForgePilot Agent

ForgePilot Agent is a Python-first local AI agent runtime and API service layer.

This repository is your own-branded project baseline for long-term development:

- Runtime core: `forgepilot_sdk`
- Service layer: `forgepilot_api`
- Compatibility goal: keep protocol stability while moving full control to Python

## What It Does

ForgePilot Agent provides:

- Plan and execute workflow (`/agent/plan` -> `/agent/execute`)
- Streaming SSE outputs
- Multi-turn tool orchestration
- Provider and sandbox management
- Session and task persistence
- MCP and Skills integration

## Current Status

Implemented and usable now:

- SDK public interfaces
  - `create_agent(options)`
  - `query({ prompt, options })`
  - `Agent.query(prompt, overrides)`
  - `Agent.prompt(prompt, overrides)`
- Provider support
  - OpenAI-compatible chat completions
  - Anthropic messages
- Full core event contract over SSE
  - `text`, `tool_use`, `tool_result`, `result`, `error`, `session`, `done`, `plan`, `direct_answer`
- SQLite persistence
  - `sessions`, `tasks`, `messages`, `files`, `settings`
  - runtime coordination tables: `runtime_sessions`, `runtime_plans`, `runtime_permissions`
- FastAPI route compatibility
  - `/agent/*`, `/sandbox/*`, `/providers/*`, `/files/*`, `/mcp/*`, `/preview/*`, `/health`, `/audit/logs`
- Skills GitHub import
  - `POST /files/import-skill` with optional `branch` and `path`
  - Auto-discovers `SKILL.md` and handles name conflicts with numeric suffixes
  - `POST /files/import-skill/self-check` for no-write import chain validation
- Codex local config fallback
  - Reads `~/.codex/config.toml` and `~/.codex/auth.json` by default
- Startup auto-hydration for model settings in frontend integration flow
- Persisted audit logs for mutating operations with query API (`GET /audit/logs`)

## Tool Coverage

Tool family currently includes:

- File/shell: `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`, `NotebookEdit`
- Web: `WebSearch`, `WebFetch`
- Agent/task/team: `Agent`, `SendMessage`, `TeamCreate`, `TeamDelete`, `Task*`
- Planning/workflow: `EnterPlanMode`, `ExitPlanMode`, `AskUserQuestion`, `ToolSearch`
- MCP resources: `ListMcpResources`, `ReadMcpResource`
- Scheduling/config: `Cron*`, `RemoteTrigger`, `Config`, `TodoWrite`
- Code intelligence: `LSP`, `Skill`

## Quick Start

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e ".[dev]"
uvicorn forgepilot_api.app:app --host 127.0.0.1 --port 2026 --reload
```

Unified PowerShell entry:

```powershell
.\scripts\dev.ps1 -Task api -Port 2026
```

One-command desktop dev (API + Tauri):

```powershell
.\scripts\dev.ps1 -Task desktop
```

Desktop dev with explicit smoke behavior:

```powershell
# strict default: require model + require plan event
.\scripts\dev.ps1 -Task desktop

# if you only want UI wiring checks without model availability enforcement
.\scripts\dev.ps1 -Task desktop -NoRequireModel
```

Legacy entrypoints are still supported for compatibility:

- `.\scripts-dev.ps1`
- `.\scripts-dev-tauri.ps1`

Windows release build with Python sidecar:

```powershell
$frontend = python scripts/resolve_frontend_shell.py --repo-root . --relative
cd $frontend
pnpm tauri:build:python:windows
```

Linux/macOS sidecar-aware build commands are also available:

```bash
frontend="$(python scripts/resolve_frontend_shell.py --repo-root . --relative)"
cd "$frontend"
pnpm tauri:build:python:linux
# or
pnpm tauri:build:python:mac-intel
pnpm tauri:build:python:mac-arm
```

Prerequisite: `python -m pip install pyinstaller`

Or run one command from repo root (tests + sidecar + checksums + Tauri build):

```powershell
.\scripts\release_windows.ps1
```

Local quality gate (ruff + tests + cargo check + sidecar build + checksums):

```powershell
.\scripts\dev.ps1 -Task verify
```

Clean stale legacy build artifacts only:

```powershell
.\scripts\clean_stale_artifacts.ps1
```

Run brand residue scan (fails if stale token is detected):

```powershell
.\scripts\check_brand_residue.ps1
```

Health check:

- [http://127.0.0.1:2026/health](http://127.0.0.1:2026/health)
- [http://127.0.0.1:2026/metrics](http://127.0.0.1:2026/metrics) (Prometheus text format)

Skills import API example:

```bash
curl -X POST http://127.0.0.1:2026/files/import-skill \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://github.com/example/skills-repo",
    "targetDir": "~/Library/Application Support/ForgePilot/skills",
    "branch": "main",
    "path": "skills/my-skill"
  }'
```

`branch` and `path` are optional. When omitted, the default branch and repository root are used.

Import self-check API example:

```bash
curl -X POST http://127.0.0.1:2026/files/import-skill/self-check \
  -H "Content-Type: application/json" \
  -d '{}'
```

Audit query example:

```bash
curl "http://127.0.0.1:2026/audit/logs?method=POST&path=/agent&limit=20"
```

## Project Layout

```text
.
|- forgepilot_sdk/                 # Runtime core (provider/tool/session/mcp)
|- forgepilot_api/                 # FastAPI service (api/services/storage/sandbox/core)
|- tests/                          # layered tests (unit/integration/contract/e2e)
|- docs/                           # architecture/compatibility/integration docs
|- .refs/                          # frozen upstream references and frontend shell
|- scripts/
|  |- dev/
|  |  |- api.ps1                   # API-only launcher
|  |  `- desktop.ps1               # API + Tauri launcher
|  |- dev.ps1                      # unified task entry (api/desktop/verify/smoke)
|  |- verify_local.ps1             # local quality gate
|  |- build_python_sidecar.py      # sidecar build
|  |- write_sidecar_checksums.py   # artifact checksums
|  |- smoke_api_chain.py           # plan -> execute -> SSE smoke check
|  `- release_windows.ps1          # one-command Windows release pipeline
|- scripts-dev.ps1                 # compatibility wrapper
`- scripts-dev-tauri.ps1           # compatibility wrapper
```

## Engineering Standards

- App factory: `forgepilot_api.app:create_app`
- ASGI app path: `forgepilot_api.app:app`
- Unified dev command: `.\scripts\dev.ps1`
- Contribution and tooling standards: `CONTRIBUTING.md`
- Engineering constraints and boundaries: `docs/engineering_standards.md`

## Runtime Settings

Environment-driven runtime options:

- `FORGEPILOT_CORS_ORIGINS`
  - Comma-separated origins, or `*` (default).
- `FORGEPILOT_CORS_ALLOW_CREDENTIALS`
  - `true/false`, default `true`.
- `FORGEPILOT_LOG_LEVEL`
  - Logging level, default `INFO`.
- `FORGEPILOT_REQUEST_ID_HEADER`
  - Request tracing header name, default `x-request-id`.
- `FORGEPILOT_EXPOSE_METRICS`
  - Enable `/metrics` endpoint, default `true`.
- `FORGEPILOT_AUTH_MODE`
  - `off` (default) or `api_key`.
- `FORGEPILOT_API_KEYS`
  - Comma-separated API keys.
  - Supports `subject:key` form for audit identity, e.g. `admin:sk-live-1,ops:sk-live-2`.
- `FORGEPILOT_API_KEY_HEADER`
  - API key header name, default `x-api-key`.
- `FORGEPILOT_AUTH_EXEMPT_PATHS`
  - Comma-separated path prefixes bypassing auth/limit.
  - Default: `/,/health,/metrics,/docs,/redoc,/openapi.json`.
- `FORGEPILOT_RATE_LIMIT_ENABLED`
  - Enable in-process request rate limiting.
- `FORGEPILOT_RATE_LIMIT_REQUESTS`
  - Max requests per window, default `60`.
- `FORGEPILOT_RATE_LIMIT_WINDOW_SECONDS`
  - Window size in seconds, default `60`.
- `FORGEPILOT_RATE_LIMIT_BACKEND`
  - `memory` (default) or `redis`.
- `FORGEPILOT_RATE_LIMIT_REDIS_URL`
  - Redis DSN when backend is `redis`, default `redis://127.0.0.1:6379/0`.
- `FORGEPILOT_RATE_LIMIT_REDIS_KEY_PREFIX`
  - Redis key prefix, default `forgepilot:ratelimit`.
- `FORGEPILOT_RATE_LIMIT_FAIL_OPEN`
  - `true` (default): Redis failure allows requests.
  - `false`: Redis failure returns `503 Rate limiter unavailable`.
- `FORGEPILOT_RATE_LIMIT_TRUST_PROXY`
  - Use forwarded IP header for identity (`false` by default).
- `FORGEPILOT_RATE_LIMIT_PROXY_HEADER`
  - Proxy source header, default `x-forwarded-for`.
- `FORGEPILOT_AUDIT_LOG_ENABLED`
  - Enable audit logs for mutating requests, default `true`.
- `FORGEPILOT_FILES_MODE`
  - Files API policy mode: `dev` or `prod`.
  - Default follows `NODE_ENV` (`production` -> `prod`, otherwise `dev`).
- `FORGEPILOT_FILES_DANGEROUS_ENABLED`
  - Enables high-risk `/files` operations (`open`, `open-in-editor`, `import-skill*`).
  - Default: `true` in `dev`, `false` in `prod`.
- `FORGEPILOT_FILES_ACL_DEFAULT`
  - Default ACL scopes for unmatched subjects.
  - `dev` default: `*`; `prod` default: `files.read`.
- `FORGEPILOT_FILES_ACL_SUBJECTS`
  - Subject-level ACL overrides, format: `subject=scopes;subject2=scopes`.
  - Example: `admin=*;operator=files.read,files.open;viewer=files.read`.
- `FORGEPILOT_RUNTIME_PLAN_TTL_SECONDS`
  - Plan cache TTL in runtime store, default `3600`.
- `FORGEPILOT_RUNTIME_PERMISSION_TTL_SECONDS`
  - Pending permission TTL in runtime store, default `1800`.
- `FORGEPILOT_PERMISSION_DECISION_TIMEOUT_SECONDS`
  - Max time to wait for permission response, default `1800`.
- `FORGEPILOT_PERMISSION_POLL_INTERVAL_SECONDS`
  - Poll interval for permission decision checks, default `0.5` (minimum `0.1`).

Redis backend prerequisite:

```bash
pip install -e ".[redis]"
```

### Files ACL scopes

You can assign ACL with grouped scopes or endpoint-level scopes:

- Group scopes:
  - `files.read` (or `read`)
  - `files.open` (or `open`)
  - `files.import` (or `import`)
- Endpoint scopes:
  - `files.readdir`, `files.stat`, `files.read`, `files.skills_dir`, `files.read_binary`, `files.detect_editor`, `files.task`
  - `files.open`, `files.open_in_editor`
  - `files.import_skill`, `files.import_skill_self_check`

Example production setup:

```bash
FORGEPILOT_FILES_MODE=prod
FORGEPILOT_FILES_DANGEROUS_ENABLED=true
FORGEPILOT_FILES_ACL_DEFAULT=files.read
FORGEPILOT_FILES_ACL_SUBJECTS=admin=*;operator=files.read,files.open,files.import;viewer=files.read
```

## Runtime State Coordination

Runtime control state is persisted in SQLite so process-local memory is no longer the only source of truth:

- `runtime_sessions`
  - Tracks active session phase and `aborted` state for stop/cancel behavior.
- `runtime_plans`
  - Stores planning output with TTL-based expiry.
- `runtime_permissions`
  - Stores pending permission requests and decision status (`pending`, `approved`, `denied`, `timeout`, `cancelled`).

Behavior model:

- Local in-memory maps are treated as short-lived acceleration caches.
- DB rows are treated as authoritative for plan existence and permission lifecycle.
- Permission waiting loop reads decision state from DB, so approval/deny can be processed across API workers.
- Plan lookup validates DB TTL first, preventing stale in-process plan cache from bypassing expiration.

## Branding Notes

The project brand is now **ForgePilot Agent**.

To keep compatibility stable, these technical identifiers are intentionally preserved:

- Python module names: `forgepilot_sdk`, `forgepilot_api`
- Existing route paths used by the current frontend integration
- Existing local data path conventions (for migration safety)

This lets you rebrand immediately without breaking active workflows.

Sidecar binary migration:

- New default binary name: `forgepilot-agent-api`
- Optional override env: `FORGEPILOT_SIDECAR_NAME`

## Test Status

Current local test suite passes:

```bash
python -m pytest -q
```

Plan/execute/SSE chain smoke test (single command):

```bash
python -m pytest -q tests/e2e/test_plan_execute_chain_smoke.py
```

Optional live network test for GitHub skill import:

```powershell
$env:FORGEPILOT_RUN_LIVE_NET_TESTS = "1"
python -m pytest -q tests/e2e/test_import_skill_live_optional.py
```

GitHub Actions workflow included:

- `.github/workflows/forgepilot-ci.yml`
  - Python tests (Ubuntu)
  - Tauri Rust compile check (`cargo check`)
  - Python sidecar build matrix (Ubuntu/Windows/macOS) + SHA256 artifact upload

## Baseline References

Frozen upstream references:

- `open-agent-sdk-typescript@main` = `366438d2ef94775a4515301bcf8a58ab866c1731`
- `shell-ui@dev` = `01818e9147585ed16558b6d64d097cebbc922e0e`

Compatibility details:

- [docs/compatibility_matrix.md](docs/compatibility_matrix.md)

## Next Steps

1. Close long-tail behavior parity gaps.
2. Optimize long-session stability and concurrency performance.
3. Add release signing/notarization pipeline for desktop artifacts.

## License Reminder

This repository is your own rewrite project.

If you plan public release or commercial distribution, review upstream licenses carefully and complete compliance checks before publishing.


