from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.resolve_frontend_shell import resolve_frontend_shell


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SHA256 checksums for sidecar artifacts."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path (default: current directory).",
    )
    parser.add_argument(
        "--pattern",
        default="forgepilot-agent-api-*",
        help="Glob pattern under frontend shell src-api/dist for artifacts.",
    )
    parser.add_argument(
        "--output",
        default=".build/sidecar-sha256.txt",
        help="Output checksum file path (relative to repo root by default).",
    )
    args = parser.parse_args()

    repo_root = Path(os.path.abspath(Path(args.repo_root).expanduser()))
    frontend_shell = resolve_frontend_shell(repo_root)
    dist_dir = Path(os.path.abspath(frontend_shell / "src-api" / "dist"))
    output_file = Path(os.path.abspath(repo_root / args.output))

    files = sorted(path for path in dist_dir.glob(args.pattern) if path.is_file())
    if not files:
        raise SystemExit(f"No artifacts matched pattern '{args.pattern}' in {dist_dir}")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for artifact in files:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        rel = artifact.relative_to(repo_root).as_posix()
        lines.append(f"{digest} *{rel}")

    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line)
    print(f"[ForgePilot] checksums written: {output_file}")


if __name__ == "__main__":
    main()
