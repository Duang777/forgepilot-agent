# Engineering Standards

## Architecture Layers

1. `forgepilot_sdk`
   - Agent runtime core, provider abstraction, tools, MCP, sessions.
2. `forgepilot_api`
   - FastAPI routes, orchestration services, storage, sandbox adapters.
   - Core runtime infrastructure lives under `forgepilot_api/core` (settings, logging, middleware, metrics).
   - Service hardening middleware includes request-id context, API-key auth, rate-limit, and audit logging.
3. `.refs/forgepilot-shell`
   - Desktop/frontend shell, integrated through stable HTTP + SSE contract.

## Module Boundaries

- `forgepilot_sdk` must not import `forgepilot_api`.
- `forgepilot_api/api/*` should stay thin and delegate to `services/*`.
- persistence access is centralized in `forgepilot_api/storage/*`.
- audit trail is persisted in SQLite (`audit_logs`) and exposed via `/audit/logs`.
- all process lifecycle hooks should be wired through `forgepilot_api/app.py`.

## Startup & Runtime Entry

- App factory: `forgepilot_api.app:create_app`
- ASGI app object: `forgepilot_api.app:app`
- CLI launch entry: `python -m forgepilot_api`
- Sidecar entry: `forgepilot_api/sidecar_entry.py`

## Dev Command Standards

- Unified command entry: `scripts/dev.ps1`
- API only: `scripts/dev/api.ps1`
- API + desktop: `scripts/dev/desktop.ps1`
- Local quality gate: `scripts/verify_local.ps1`

## CI Baseline

- Ruff lint + format checks required.
- Python tests required.
- Rust `cargo check` required for Tauri shell.
- Sidecar build + checksum artifact required for release confidence.

## Test Layout

- `tests/unit`: focused logic tests.
- `tests/integration`: route/service integration and persistence behavior.
- `tests/contract`: request/response/SSE contract checks.
- `tests/e2e`: plan/execute/smoke chain and live optional scenarios.

## Rate Limit Backends

- Default backend: `memory` (single-instance friendly).
- Optional backend: `redis` for shared counters across multiple API instances.
- Redis outage policy is controlled by `FORGEPILOT_RATE_LIMIT_FAIL_OPEN`.

## Naming and Branding

- Project name: `forgepilot-agent`
- Package names: `forgepilot_sdk`, `forgepilot_api`
- Deprecated transitional names are not allowed in source.
