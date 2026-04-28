from __future__ import annotations

import argparse
from pathlib import Path

from forgepilot_api.ops.parity import build_parity_summary, render_parity_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate parity report markdown.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path.",
    )
    parser.add_argument(
        "--output",
        default="docs/parity_report.md",
        help="Output markdown file path.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero if full baseline parity is not reached.",
    )
    parser.add_argument(
        "--strict-semantic",
        action="store_true",
        help="Return non-zero if semantic harness baseline is not reached.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    output_path = (repo_root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = build_parity_summary(repo_root)
    report = render_parity_report(summary)
    output_path.write_text(report, encoding="utf-8")
    print(f"parity report written to {output_path}")
    if args.strict and not summary.is_full_parity:
        print("strict mode failed: baseline parity is PARTIAL")
        return 2
    if args.strict_semantic and not summary.is_semantic_baseline:
        print("strict semantic mode failed: semantic baseline is PARTIAL")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
