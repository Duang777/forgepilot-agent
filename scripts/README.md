# Scripts Index

## Primary Entry

- `dev.py` (cross-platform primary entry)
  - `api`: run FastAPI locally.
  - `desktop`: run API + Tauri desktop shell.
  - `smoke`: run API smoke chain (`/agent/plan` -> `/agent/execute`).
  - `verify`: run full local quality gate.
  - `parity`: generate parity report (`docs/parity_report.md`).
  - `sidecar`: build sidecar and generate checksum file.

## PowerShell Compatibility Layer

- `dev.ps1`
  - Keeps legacy Windows task syntax and delegates to `dev.py`.
- `verify_local.ps1`
  - Keeps legacy Windows verify flags and delegates to `dev.py verify`.

## Dev Launchers

- `dev/api.ps1`: API-only launcher.
- `dev/desktop.ps1`: desktop integrated launcher.

## Quality and Release

- `verify_local.ps1`: local quality gate wrapper (`dev.py verify`).
- `release_windows.ps1`: Windows release pipeline.
- `build_python_sidecar.py`: sidecar binary build.
- `write_sidecar_checksums.py`: sidecar checksum generation.

## Maintenance Utilities

- `resolve_frontend_shell.py`: locate frontend shell root from repo.
- `scan_brand_residue.py`: brand residue scanner.
- `check_brand_residue.ps1`: scanner wrapper with failure-on-hit behavior.
- `clean_stale_artifacts.ps1`: remove stale build artifacts.
