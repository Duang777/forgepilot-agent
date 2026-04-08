from __future__ import annotations

from pathlib import Path
from typing import Any


def _parse_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---", 4)
    if end == -1:
        return {}
    raw = content[4:end]
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def load_skills_from_dir(root: Path) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return registry
    for skill_dir in root.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        file = skill_dir / "SKILL.md"
        if not file.exists():
            continue
        content = file.read_text(encoding="utf-8", errors="replace")
        metadata = _parse_frontmatter(content)
        name = str(metadata.get("name") or skill_dir.name).strip().lower()
        registry[name] = {
            "name": name,
            "description": metadata.get("description", ""),
            "content": content,
            "path": str(skill_dir),
        }
    return registry


def load_default_skill_registry() -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for root in [Path.home() / ".claude" / "skills", Path.home() / ".forgepilot" / "skills"]:
        registry.update(load_skills_from_dir(root))
    return registry


def load_skill_registry_from_paths(paths: list[str] | list[Path]) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for root in paths:
        path = root if isinstance(root, Path) else Path(root).expanduser()
        registry.update(load_skills_from_dir(path))
    return registry
