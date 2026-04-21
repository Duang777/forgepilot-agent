# AGENTS.md — forgepilot-agent

> Agent playbook for this repository. Keep instructions command-first and executable.

## 1. Stack
- Python service/runtime: `forgepilot_api`, `forgepilot_sdk`
- Frontend shell (reference workspace): `.refs/forgepilot-shell`
- Python tooling: `uv` + `ruff` + `mypy` + `pytest`
- Node tooling (frontend shell): `pnpm`
- Rust tooling (Tauri): `cargo`

## 2. Setup (project-local, no global installs)
Run from repo root:

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/uv/python"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
export PNPM_STORE_DIR="$PWD/.cache/pnpm-store"

uv sync --extra dev
```

## 3. Daily commands
Primary entrypoint:

```bash
uv run python scripts/dev.py api --host 127.0.0.1 --port 2026
uv run python scripts/dev.py smoke --base-url http://127.0.0.1:2026 --require-plan
uv run python scripts/dev.py desktop --api-host 127.0.0.1 --api-port 2026
uv run python scripts/dev.py verify
uv run python scripts/dev.py parity --strict
```

Windows compatibility entrypoint:

```powershell
.\scripts\dev.ps1 -Task api
.\scripts\dev.ps1 -Task desktop
.\scripts\dev.ps1 -Task verify
.\scripts\dev.ps1 -Task smoke -NoRequireModel
```

## 4. Definition of Done
Task is not done until all commands exit `0`:
1. `uv run python -m mypy scripts/dev.py scripts/build_python_sidecar.py scripts/write_sidecar_checksums.py scripts/resolve_frontend_shell.py`
2. `uv run python -m ruff check .`
3. `uv run python -m pytest -q`
4. If touched Tauri/Desktop flow: `uv run python scripts/dev.py verify --skip-sidecar-build --skip-brand-residue-check`
5. No leftover debug output (`print`, `console.log`, commented-out code) in modified files

## 5. Constraints
- Do not use `sudo`, global package installs, or destructive git/database commands.
- Do not modify secrets (`.env*`, `*.pem`, `*.key`) unless explicitly requested.
- Prefer `rg`, `fd`, `jq` for search and inspection.
- Keep changes minimal and scoped to task intent.

## 6. Reporting format
When done, report in this order:
1. Summary
2. Changed files
3. Verification commands and exit codes
4. Deferred items and reasons
5. Risks / rollback (if config/build/workflow changed)
