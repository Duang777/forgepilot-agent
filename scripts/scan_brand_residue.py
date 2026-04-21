from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DEFAULT_ROOTS = [
    "forgepilot_api",
    "forgepilot_sdk",
    "tests",
    "scripts",
    "docs",
    ".refs/forgepilot-shell/src",
    ".refs/forgepilot-shell/src-tauri/src",
]

DEFAULT_TOKENS = ["workany"]

EXCLUDED_DIR_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "target",
    "dist",
    "build",
    ".build",
    "sessions",
    ".runlogs",
}

EXCLUDED_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".woff",
    ".woff2",
    ".ttf",
}

ALLOWLIST: dict[str, set[str]] = {
    "workany": {
        "scripts/clean_stale_artifacts.ps1",
        "scripts/scan_brand_residue.py",
        "docs/0407.md",
        "tests/unit/test_upstream_workany_api_parity.py",
    }
}


def _is_binary(file_path: Path) -> bool:
    try:
        with file_path.open("rb") as fp:
            sample = fp.read(2048)
    except OSError:
        return True
    return b"\x00" in sample


def _scan_file(file_path: Path, tokens: list[str], repo_root: Path) -> list[dict[str, object]]:
    lower_tokens = [(token, token.lower()) for token in tokens]
    rel_path = file_path.relative_to(repo_root).as_posix()
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    hits: list[dict[str, object]] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        lower_line = line.lower()
        for raw_token, token in lower_tokens:
            idx = lower_line.find(token)
            if idx < 0:
                continue
            allowlisted_paths = ALLOWLIST.get(token, set())
            if rel_path in allowlisted_paths:
                continue
            hits.append(
                {
                    "path": rel_path,
                    "line": line_no,
                    "column": idx + 1,
                    "token": raw_token,
                    "snippet": line.strip(),
                }
            )
    return hits


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirs, current_files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_NAMES]
        base = Path(current_root)
        for filename in current_files:
            candidate = base / filename
            if candidate.suffix.lower() in EXCLUDED_SUFFIXES:
                continue
            if candidate.name.startswith(".") and candidate.suffix not in {".ts", ".tsx", ".py", ".md", ".ps1", ".toml", ".json"}:
                continue
            files.append(candidate)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan source directories for stale brand residue tokens."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Relative root to scan. Repeatable. Defaults to curated source roots.",
    )
    parser.add_argument(
        "--token",
        action="append",
        dest="tokens",
        help="Case-insensitive token to detect. Repeatable.",
    )
    parser.add_argument(
        "--output",
        default=".build/verify/brand-residue-report.json",
        help="Output report path relative to repo root.",
    )
    parser.add_argument(
        "--fail-on-hit",
        action="store_true",
        help="Return non-zero when residue hits are found.",
    )
    args = parser.parse_args()

    repo_root = Path(os.path.abspath(Path(args.repo_root).expanduser()))
    roots = args.roots or DEFAULT_ROOTS
    tokens = args.tokens or DEFAULT_TOKENS

    all_hits: list[dict[str, object]] = []
    scanned_roots: list[str] = []
    scanned_files = 0
    skipped_binary = 0

    for rel_root in roots:
        abs_root = (repo_root / rel_root).resolve()
        if not abs_root.exists() or not abs_root.is_dir():
            continue
        scanned_roots.append(abs_root.relative_to(repo_root).as_posix())
        for file_path in _iter_files(abs_root):
            scanned_files += 1
            if _is_binary(file_path):
                skipped_binary += 1
                continue
            all_hits.extend(_scan_file(file_path, tokens, repo_root))

    report = {
        "success": True,
        "tokens": tokens,
        "roots": scanned_roots,
        "scannedFiles": scanned_files,
        "skippedBinaryFiles": skipped_binary,
        "hitCount": len(all_hits),
        "hits": all_hits,
    }

    output_path = (repo_root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[ForgePilot] Brand residue scan complete: {output_path}")
    print(f"[ForgePilot] scanned files: {scanned_files} (binary skipped: {skipped_binary})")
    print(f"[ForgePilot] residue hits: {len(all_hits)}")
    if all_hits:
        sample = all_hits[0]
        print(
            "[ForgePilot] sample hit: "
            f"{sample['path']}:{sample['line']}:{sample['column']} token={sample['token']}"
        )

    if args.fail_on_hit and all_hits:
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
