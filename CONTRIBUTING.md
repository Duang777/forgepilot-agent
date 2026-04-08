# Contributing Guide

## Engineering Baseline

- Python: `3.12` (runtime minimum remains `>=3.10`).
- Frontend shell: managed inside `.refs/forgepilot-shell`.
- Package modules:
  - `forgepilot_sdk`: runtime core.
  - `forgepilot_api`: FastAPI service surface.

## Local Setup

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e ".[dev]"
```

## Daily Commands

```powershell
.\scripts\dev.ps1 -Task api
.\scripts\dev.ps1 -Task desktop
.\scripts\dev.ps1 -Task verify
.\scripts\dev.ps1 -Task smoke -NoRequireModel
```

Legacy shortcuts are preserved:

- `.\scripts-dev.ps1`
- `.\scripts-dev-tauri.ps1`

## Quality Gates

Before creating a release candidate, run:

```powershell
.\scripts\verify_local.ps1
```

This includes:

- Brand residue check
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
