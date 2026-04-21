from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

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


def _cmd_display(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _run(cmd: list[str], cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> None:
    print(f"[ForgePilot] $ {_cmd_display(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def _resolve_frontend_shell_dir(frontend_shell_dir: str) -> Path:
    if frontend_shell_dir.strip():
        path = (REPO_ROOT / frontend_shell_dir).resolve()
    else:
        path = resolve_frontend_shell(REPO_ROOT).resolve()
    if not path.exists():
        raise SystemExit(f"[ForgePilot] Frontend shell path not found: {path}")
    return path


def _wait_for_api_health(api_url: str, retries: int = 40, interval_seconds: float = 0.5) -> bool:
    health_url = f"{api_url.rstrip('/')}/health"
    for _ in range(retries):
        try:
            with urlopen(health_url, timeout=2) as response:  # noqa: S310 - local dev endpoint
                if response.status == 200:
                    return True
        except (URLError, HTTPError):
            time.sleep(interval_seconds)
    return False


def _clean_stale_artifacts(binary_name: str) -> None:
    pyinstaller_root = REPO_ROOT / ".build" / "pyinstaller"
    if not pyinstaller_root.exists():
        print("[ForgePilot] No .build/pyinstaller directory found. Nothing to clean.")
        return

    temp_name = f"{binary_name}-temp"
    maybe_targets = [
        pyinstaller_root / "dist" / temp_name,
        pyinstaller_root / "dist" / f"{temp_name}.exe",
        pyinstaller_root / "spec" / f"{temp_name}.spec",
        pyinstaller_root / "work" / temp_name,
    ]
    for target in maybe_targets:
        if not target.exists():
            continue
        resolved = target.resolve()
        if pyinstaller_root.resolve() not in resolved.parents:
            raise SystemExit(f"[ForgePilot] Refusing to clean outside pyinstaller root: {resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        print(f"[ForgePilot] Removed stale artifact: {resolved}")
    print("[ForgePilot] Stale artifact cleanup complete.")


def _cmd_api(args: argparse.Namespace) -> None:
    env = dict(os.environ)
    env["PORT"] = str(args.port)
    _run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "forgepilot_api.app:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--reload",
        ],
        env=env,
    )


def _cmd_smoke(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        "scripts/smoke_api_chain.py",
        "--base-url",
        args.base_url,
        "--timeout-sec",
        str(args.timeout_sec),
    ]
    if args.require_model:
        cmd.append("--require-model")
    if args.require_plan:
        cmd.append("--require-plan")
    if args.api_key:
        cmd.extend(["--api-key", args.api_key])
    if args.model:
        cmd.extend(["--model", args.model])
    if args.base_url_override:
        cmd.extend(["--base-url-override", args.base_url_override])
    if args.api_type:
        cmd.extend(["--api-type", args.api_type])
    _run(cmd)


def _cmd_desktop(args: argparse.Namespace) -> None:
    frontend_shell = _resolve_frontend_shell_dir(args.frontend_shell_dir)
    api_url = f"http://{args.api_host}:{args.api_port}"

    print(f"[ForgePilot] Starting Python API on {api_url} ...")
    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "forgepilot_api.app:app",
            "--host",
            args.api_host,
            "--port",
            str(args.api_port),
        ],
        cwd=REPO_ROOT,
    )

    try:
        if not _wait_for_api_health(api_url):
            raise SystemExit(f"[ForgePilot] API health check failed: {api_url}/health")

        if args.run_smoke_check:
            smoke_cmd = [
                sys.executable,
                "scripts/smoke_api_chain.py",
                "--base-url",
                api_url,
                "--timeout-sec",
                "120",
                "--require-plan",
            ]
            if args.require_model:
                smoke_cmd.append("--require-model")
            _run(smoke_cmd)

        env = dict(os.environ)
        env["VITE_API_BASE_URL"] = api_url
        _run(["pnpm", "tauri", "dev"], cwd=frontend_shell, env=env)
    finally:
        if api_proc.poll() is None:
            if args.stop_api_on_exit:
                print(f"[ForgePilot] Stopping Python API (PID {api_proc.pid}) ...")
                api_proc.terminate()
                try:
                    api_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    api_proc.kill()
            else:
                print(f"[ForgePilot] Keeping Python API running on {api_url} (PID {api_proc.pid}).")


def _cmd_verify(args: argparse.Namespace) -> None:
    frontend_shell = _resolve_frontend_shell_dir(args.frontend_shell_dir)
    print(f"[ForgePilot] Repo root: {REPO_ROOT}")
    print(f"[ForgePilot] Frontend shell: {frontend_shell}")

    if not args.skip_artifact_cleanup:
        _clean_stale_artifacts(args.binary_name)

    if not args.skip_brand_residue_check:
        _run(
            [
                sys.executable,
                "scripts/scan_brand_residue.py",
                "--repo-root",
                str(REPO_ROOT),
                "--output",
                ".build/verify/brand-residue-report.json",
                "--fail-on-hit",
            ]
        )

    if not args.skip_lint:
        _run([sys.executable, "-m", "ruff", "check", "."])

    if not args.skip_typecheck:
        _run(
            [
                sys.executable,
                "-m",
                "mypy",
                "scripts/dev.py",
                "scripts/build_python_sidecar.py",
                "scripts/write_sidecar_checksums.py",
                "scripts/resolve_frontend_shell.py",
            ]
        )

    if not args.skip_tests:
        _run([sys.executable, "-m", "pytest", "-q"])

    if not args.skip_cargo_check:
        _run(["cargo", "check"], cwd=frontend_shell / "src-tauri")

    if not args.skip_sidecar_build:
        _run(
            [
                sys.executable,
                "scripts/build_python_sidecar.py",
                "--repo-root",
                ".",
                "--target-triple",
                args.target_triple,
                "--binary-name",
                args.binary_name,
            ]
        )

    if not args.skip_checksum:
        pattern = f"{args.binary_name}-{args.target_triple}*"
        _run(
            [
                sys.executable,
                "scripts/write_sidecar_checksums.py",
                "--repo-root",
                ".",
                "--pattern",
                pattern,
                "--output",
                f".build/verify/sidecar-sha256-{args.target_triple}.txt",
            ]
        )

    print("[ForgePilot] Verification completed.")


def _cmd_parity(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        "scripts/generate_parity_report.py",
        "--repo-root",
        ".",
        "--output",
        "docs/parity_report.md",
    ]
    if args.strict:
        cmd.append("--strict")
    _run(cmd)


def _cmd_sidecar(args: argparse.Namespace) -> None:
    _run(
        [
            sys.executable,
            "scripts/build_python_sidecar.py",
            "--repo-root",
            ".",
            "--target-triple",
            args.target_triple,
            "--binary-name",
            args.binary_name,
        ]
    )
    if args.skip_checksum:
        return
    _run(
        [
            sys.executable,
            "scripts/write_sidecar_checksums.py",
            "--repo-root",
            ".",
            "--pattern",
            f"{args.binary_name}-{args.target_triple}*",
            "--output",
            args.checksum_output,
        ]
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-platform dev entrypoint for ForgePilot Agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    api = subparsers.add_parser("api", help="Run FastAPI locally.")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=2026)
    api.set_defaults(handler=_cmd_api)

    smoke = subparsers.add_parser("smoke", help="Run API smoke chain (/agent/plan -> /agent/execute).")
    smoke.add_argument("--base-url", default="http://127.0.0.1:2026")
    smoke.add_argument("--timeout-sec", type=int, default=90)
    smoke.add_argument("--api-key", default="")
    smoke.add_argument("--model", default="")
    smoke.add_argument("--base-url-override", default="")
    smoke.add_argument("--api-type", choices=["", "openai-completions", "anthropic-messages"], default="")
    smoke.add_argument("--require-model", action="store_true")
    smoke.add_argument("--require-plan", action="store_true")
    smoke.set_defaults(handler=_cmd_smoke)

    desktop = subparsers.add_parser("desktop", help="Run API + Tauri desktop shell.")
    desktop.add_argument("--api-host", default="127.0.0.1")
    desktop.add_argument("--api-port", type=int, default=2026)
    desktop.add_argument("--frontend-shell-dir", default="")
    desktop.add_argument("--run-smoke-check", dest="run_smoke_check", action="store_true")
    desktop.add_argument("--no-smoke-check", dest="run_smoke_check", action="store_false")
    desktop.set_defaults(run_smoke_check=True)
    desktop.add_argument("--require-model", dest="require_model", action="store_true")
    desktop.add_argument("--no-require-model", dest="require_model", action="store_false")
    desktop.set_defaults(require_model=True)
    desktop.add_argument("--stop-api-on-exit", action="store_true")
    desktop.set_defaults(handler=_cmd_desktop)

    verify = subparsers.add_parser("verify", help="Run local quality gate.")
    verify.add_argument("--frontend-shell-dir", default="")
    verify.add_argument("--target-triple", default=_default_target_triple())
    verify.add_argument("--binary-name", default="forgepilot-agent-api")
    verify.add_argument("--skip-lint", action="store_true")
    verify.add_argument("--skip-typecheck", action="store_true")
    verify.add_argument("--skip-tests", action="store_true")
    verify.add_argument("--skip-cargo-check", action="store_true")
    verify.add_argument("--skip-sidecar-build", action="store_true")
    verify.add_argument("--skip-artifact-cleanup", action="store_true")
    verify.add_argument("--skip-brand-residue-check", action="store_true")
    verify.add_argument("--skip-checksum", action="store_true")
    verify.set_defaults(handler=_cmd_verify)

    parity = subparsers.add_parser("parity", help="Generate parity report.")
    parity.add_argument("--strict", action="store_true")
    parity.set_defaults(handler=_cmd_parity)

    sidecar = subparsers.add_parser("sidecar", help="Build Python sidecar and optionally generate checksums.")
    sidecar.add_argument("--target-triple", default=_default_target_triple())
    sidecar.add_argument("--binary-name", default="forgepilot-agent-api")
    sidecar.add_argument("--checksum-output", default=".build/sidecar-sha256.txt")
    sidecar.add_argument("--skip-checksum", action="store_true")
    sidecar.set_defaults(handler=_cmd_sidecar)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
