from __future__ import annotations

import importlib
import re
from pathlib import Path


def _load_upstream_runtime_exports() -> set[str]:
    upstream_index = Path(r"D:\DUAN\APP\_upstream\open-agent-sdk-typescript\src\index.ts").read_text(encoding="utf-8")
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
    sdk = importlib.import_module("forgepilot_sdk")
    runtime_exports = set(getattr(sdk, "__all__", []))
    upstream_exports = _load_upstream_runtime_exports()
    missing = sorted(name for name in upstream_exports if name not in runtime_exports)
    assert missing == []

