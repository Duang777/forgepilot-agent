from __future__ import annotations

import importlib
import os
import re
from pathlib import Path

import pytest


def _resolve_upstream_index_path() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    from_env_index = os.getenv("FORGEPILOT_UPSTREAM_OPEN_AGENT_SDK_INDEX", "").strip()
    if from_env_index:
        return Path(from_env_index).expanduser()

    from_env_root = os.getenv("FORGEPILOT_UPSTREAM_OPEN_AGENT_SDK_ROOT", "").strip()
    if from_env_root:
        return Path(from_env_root).expanduser() / "src" / "index.ts"

    bundled = repo_root / ".refs" / "open-agent-sdk-typescript" / "src" / "index.ts"
    if bundled.exists():
        return bundled

    legacy = Path(r"D:\DUAN\APP\_upstream\open-agent-sdk-typescript\src\index.ts")
    if legacy.exists():
        return legacy
    return None


def _load_upstream_runtime_exports(upstream_index_path: Path) -> set[str]:
    upstream_index = upstream_index_path.read_text(encoding="utf-8")
    names: set[str] = set()
    for match in re.finditer(r"export\s*\{([^}]*)\}", upstream_index, flags=re.S):
        body = match.group(1)
        for raw in body.split(","):
            name = re.sub(r"//.*", "", raw).strip()
            if not name:
                continue
            if " as " in name:
                name = name.split(" as ")[-1].strip()
            if name:
                names.add(name)
    return names


def test_runtime_export_surface_matches_upstream_index_exports() -> None:
    upstream_index_path = _resolve_upstream_index_path()
    if upstream_index_path is None or not upstream_index_path.exists():
        pytest.skip("open-agent-sdk-typescript upstream index.ts not found")

    sdk = importlib.import_module("forgepilot_sdk")
    runtime_exports = set(getattr(sdk, "__all__", []))
    upstream_exports = _load_upstream_runtime_exports(upstream_index_path)
    missing = sorted(name for name in upstream_exports if name not in runtime_exports)
    assert missing == []
