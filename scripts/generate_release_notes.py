from __future__ import annotations

import argparse
import re
from pathlib import Path


def _extract_section(changelog_text: str, version: str) -> str:
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\](?:\s*-\s*.+)?\n(?P<body>[\s\S]*?)(?=^## \[|\Z)",
        re.MULTILINE,
    )
    match = pattern.search(changelog_text)
    if match:
        return match.group("body").strip()

    unreleased = re.compile(
        r"^## \[Unreleased\]\n(?P<body>[\s\S]*?)(?=^## \[|\Z)",
        re.MULTILINE,
    ).search(changelog_text)
    if unreleased:
        return (
            "Version section not found in CHANGELOG; using [Unreleased] as fallback.\n\n"
            + unreleased.group("body").strip()
        )
    return "No changelog notes available for this release."


def build_release_notes(changelog_path: Path, version: str) -> str:
    changelog = changelog_path.read_text(encoding="utf-8")
    section = _extract_section(changelog, version)
    return f"## ForgePilot Agent {version}\n\n{section}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GitHub release notes from CHANGELOG.md.")
    parser.add_argument("--changelog", default="CHANGELOG.md", help="Path to changelog file.")
    parser.add_argument("--version", required=True, help="Release version (for example: 0.2.0).")
    parser.add_argument("--output", required=True, help="Output markdown path.")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    notes = build_release_notes(Path(args.changelog), args.version.strip())
    output_path.write_text(notes, encoding="utf-8")
    print(f"release notes written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
