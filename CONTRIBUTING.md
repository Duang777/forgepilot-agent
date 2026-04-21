# Contributing Guide

## Engineering Baseline

- Python: `3.12` (runtime minimum remains `>=3.10`).
- Frontend shell: managed inside `.refs/forgepilot-shell`.
- Local prerequisites: Python/Node(pnpm)/Rust can be installed by any toolchain manager.
- Package modules:
  - `forgepilot_sdk`: runtime core.
  - `forgepilot_api`: FastAPI service surface.
- Python workflow: `uv` (`uv sync`, `uv run`).

## Local Setup

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/uv/python"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
export PNPM_STORE_DIR="$PWD/.cache/pnpm-store"

uv sync --extra dev
```

## Daily Commands

```bash
uv run python scripts/dev.py api --host 127.0.0.1 --port 2026
uv run python scripts/dev.py desktop --api-host 127.0.0.1 --api-port 2026
uv run python scripts/dev.py verify
uv run python scripts/dev.py smoke --base-url http://127.0.0.1:2026 --require-plan
```

Windows compatibility wrappers are preserved:

- `.\scripts-dev.ps1`
- `.\scripts-dev-tauri.ps1`
- `.\scripts\dev.ps1`

## Quality Gates

Before creating a release candidate, run:

```bash
uv run python scripts/dev.py verify
```

This includes:

- Brand residue check
- Mypy type check
- Python test suite
- Tauri `cargo check`
- Sidecar build and checksum generation

Test directories are organized as:

- `tests/unit`
- `tests/integration`
- `tests/contract`
- `tests/e2e`

## Code Style

- `ruff` is the default lint/format tool.
- `.editorconfig` is authoritative for line endings and indentation.
- Use `pre-commit` to enforce consistency:

```bash
pre-commit install
pre-commit run --all-files
python -m ruff check .
```
