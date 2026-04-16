from __future__ import annotations

from typing import Any

_SKILLS: dict[str, dict[str, Any]] = {}
_ALIASES: dict[str, str] = {}


def register_skill(definition: dict[str, Any]) -> None:
    name = str(definition.get("name") or "").strip()
    if not name:
        raise ValueError("Skill definition requires a non-empty 'name'.")
    _SKILLS[name] = dict(definition)
    for alias in definition.get("aliases") or []:
        alias_key = str(alias or "").strip()
        if alias_key:
            _ALIASES[alias_key] = name


def get_skill(name: str) -> dict[str, Any] | None:
    if name in _SKILLS:
        return _SKILLS[name]
    resolved = _ALIASES.get(name)
    if resolved:
        return _SKILLS.get(resolved)
    return None


def get_all_skills() -> list[dict[str, Any]]:
    return [dict(v) for v in _SKILLS.values()]


def get_user_invocable_skills() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for skill in _SKILLS.values():
        if skill.get("userInvocable", True) is False:
            continue
        is_enabled = skill.get("isEnabled")
        if callable(is_enabled):
            try:
                if not bool(is_enabled()):
                    continue
            except Exception:
                continue
        result.append(dict(skill))
    return result


def has_skill(name: str) -> bool:
    return name in _SKILLS or name in _ALIASES


def unregister_skill(name: str) -> bool:
    target = name
    if target not in _SKILLS:
        target = _ALIASES.get(name) or ""
    if target not in _SKILLS:
        return False
    skill = _SKILLS.pop(target)
    for alias in skill.get("aliases") or []:
        _ALIASES.pop(str(alias), None)
    for alias, resolved in list(_ALIASES.items()):
        if resolved == target:
            _ALIASES.pop(alias, None)
    return True


def clear_skills() -> None:
    _SKILLS.clear()
    _ALIASES.clear()


def format_skills_for_prompt(context_window_tokens: int | None = None) -> str:
    invocable = get_user_invocable_skills()
    if not invocable:
        return ""

    chars_per_token = 4
    default_budget = 8000
    max_desc_chars = 250
    budget = int(context_window_tokens * 0.01 * chars_per_token) if context_window_tokens else default_budget

    used = 0
    lines: list[str] = []
    for skill in invocable:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        desc = str(skill.get("description") or "")
        if len(desc) > max_desc_chars:
            desc = desc[:max_desc_chars] + "..."
        trigger = str(skill.get("whenToUse") or "")
        tail = f" TRIGGER when: {trigger}" if trigger else ""
        line = f"- {name}: {desc}{tail}"
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def registerSkill(definition: dict[str, Any]) -> None:
    register_skill(definition)


def getSkill(name: str) -> dict[str, Any] | None:
    return get_skill(name)


def getAllSkills() -> list[dict[str, Any]]:
    return get_all_skills()


def getUserInvocableSkills() -> list[dict[str, Any]]:
    return get_user_invocable_skills()


def hasSkill(name: str) -> bool:
    return has_skill(name)


def unregisterSkill(name: str) -> bool:
    return unregister_skill(name)


def clearSkills() -> None:
    clear_skills()


def formatSkillsForPrompt(contextWindowTokens: int | None = None) -> str:
    return format_skills_for_prompt(contextWindowTokens)
