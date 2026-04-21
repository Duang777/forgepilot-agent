from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.resolve_frontend_shell import resolve_frontend_shell


def _default_target_triple() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        return "x86_64-pc-windows-msvc" if "64" in machine else "i686-pc-windows-msvc"
    if system == "darwin":
        return "aarch64-apple-darwin" if "arm" in machine else "x86_64-apple-darwin"
    return "x86_64-unknown-linux-gnu"


def _run(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def _abs_no_resolve(path: Path) -> Path:
    return Path(os.path.abspath(path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Python API sidecar binary for Tauri packaging."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path (default: current directory).",
    )
    parser.add_argument(
        "--target-triple",
        default=_default_target_triple(),
        help="Rust target triple suffix for Tauri externalBin naming.",
    )
    parser.add_argument(
        "--binary-name",
        default="forgepilot-agent-api",
        help="Base sidecar binary name expected by Tauri sidecar() calls.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory for generated sidecar binary (auto-detected when omitted).",
    )
    parser.add_argument(
        "--install-pyinstaller",
        action="store_true",
        help="Install PyInstaller before build.",
    )
    args = parser.parse_args()

    repo_root = _abs_no_resolve(Path(args.repo_root).expanduser())
    if args.output_dir:
        output_dir = _abs_no_resolve(repo_root / args.output_dir)
    else:
        frontend_shell = resolve_frontend_shell(repo_root)
        output_dir = _abs_no_resolve(frontend_shell / "src-api" / "dist")
    build_dir = _abs_no_resolve(repo_root / ".build" / "pyinstaller")
    spec_dir = build_dir / "spec"
    tmp_dist_dir = build_dir / "dist"

    sidecar_entry = repo_root / "forgepilot_api" / "sidecar_entry.py"
    if not sidecar_entry.exists():
        raise SystemExit(f"sidecar entry not found: {sidecar_entry}")

    if args.install_pyinstaller:
        _run([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0.0"], cwd=repo_root)

    # Check PyInstaller availability early with a clear message.
    check = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise SystemExit(
            "PyInstaller is not available. Run with --install-pyinstaller "
            "or install manually: python -m pip install pyinstaller"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    tmp_dist_dir.mkdir(parents=True, exist_ok=True)

    temp_name = f"{args.binary_name}-temp"
    final_name = f"{args.binary_name}-{args.target_triple}"
    if platform.system().lower() == "windows":
        final_name += ".exe"

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            temp_name,
            "--distpath",
            str(tmp_dist_dir),
            "--workpath",
            str(build_dir / "work"),
            "--specpath",
            str(spec_dir),
            str(sidecar_entry),
        ],
        cwd=repo_root,
    )

    produced = tmp_dist_dir / (temp_name + (".exe" if platform.system().lower() == "windows" else ""))
    if not produced.exists():
        raise SystemExit(f"expected output binary not found: {produced}")

    target_path = output_dir / final_name
    if target_path.exists():
        target_path.unlink()
    shutil.copy2(produced, target_path)

    print(f"[ForgePilot] sidecar built: {target_path}")


if __name__ == "__main__":
    main()
