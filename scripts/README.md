# Scripts Index

## Primary Entry

- `dev.ps1`
  - `-Task api`: run FastAPI locally.
  - `-Task desktop`: run API + Tauri desktop shell.
  - `-Task smoke`: run API smoke chain (`/agent/plan` -> `/agent/execute`).
  - `-Task verify`: run full local quality gate.
  - `-NoRequireModel`: optional flag for `desktop` and `smoke` when model config is unavailable.

## Dev Launchers

- `dev/api.ps1`: API-only launcher.
- `dev/desktop.ps1`: desktop integrated launcher.

## Quality and Release

- `verify_local.ps1`: local quality gate (ruff + pytest + cargo check + sidecar build).
- `release_windows.ps1`: Windows release pipeline.
- `build_python_sidecar.py`: sidecar binary build.
- `write_sidecar_checksums.py`: sidecar checksum generation.

## Maintenance Utilities

- `resolve_frontend_shell.py`: locate frontend shell root from repo.
- `scan_brand_residue.py`: brand residue scanner.
- `check_brand_residue.ps1`: scanner wrapper with failure-on-hit behavior.
- `clean_stale_artifacts.ps1`: remove stale build artifacts.
