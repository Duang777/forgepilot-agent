from __future__ import annotations

import argparse
from pathlib import Path


def resolve_frontend_shell(repo_root: Path) -> Path:
    refs_root = (repo_root / ".refs").resolve()
    if not refs_root.exists():
        raise SystemExit(f".refs directory not found: {refs_root}")

    candidates: list[Path] = []
    for entry in refs_root.iterdir():
        if not entry.is_dir():
            continue
        if (entry / "package.json").exists() and (entry / "src-tauri").is_dir() and (entry / "src-api").is_dir():
            candidates.append(entry)

    if not candidates:
        raise SystemExit(f"No frontend shell found under {refs_root}")
    if len(candidates) == 1:
        return candidates[0]

    # Prefer directories with project-aligned naming when possible.
    preferred = sorted(candidates, key=lambda p: ("forgepilot" not in p.name.lower(), p.name.lower()))
    for candidate in preferred:
        if (candidate / "src-tauri" / "tauri.conf.python-sidecar.json").exists():
            return candidate

    # Stable fallback: deterministic alphabetical order.
    return sorted(candidates)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve frontend shell directory under .refs.")
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument(
        "--relative",
        action="store_true",
        help="Print path relative to repo root instead of absolute path.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    shell_dir = resolve_frontend_shell(repo_root)
    if args.relative:
        print(shell_dir.relative_to(repo_root).as_posix())
    else:
        print(shell_dir)


if __name__ == "__main__":
    main()
