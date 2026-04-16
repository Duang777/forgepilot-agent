from forgepilot_sdk.skills.loader import (
    load_default_skill_registry,
    load_skill_registry_from_paths,
    load_skills_from_dir,
)
from forgepilot_sdk.skills.registry import (
    clearSkills,
    clear_skills,
    formatSkillsForPrompt,
    format_skills_for_prompt,
    getAllSkills,
    getSkill,
    getUserInvocableSkills,
    get_all_skills,
    get_skill,
    get_user_invocable_skills,
    hasSkill,
    has_skill,
    registerSkill,
    register_skill,
    unregisterSkill,
    unregister_skill,
)

_BUNDLED_INITIALIZED = False


def init_bundled_skills() -> None:
    global _BUNDLED_INITIALIZED
    if _BUNDLED_INITIALIZED:
        return
    default_registry = load_default_skill_registry()
    for name, payload in default_registry.items():
        register_skill(
            {
                "name": payload.get("name") or name,
                "description": payload.get("description") or "",
                "content": payload.get("content") or "",
                "path": payload.get("path"),
                "userInvocable": True,
            }
        )
    _BUNDLED_INITIALIZED = True


def initBundledSkills() -> None:
    init_bundled_skills()


__all__ = [
    "load_default_skill_registry",
    "load_skills_from_dir",
    "load_skill_registry_from_paths",
    "register_skill",
    "get_skill",
    "get_all_skills",
    "get_user_invocable_skills",
    "has_skill",
    "unregister_skill",
    "clear_skills",
    "format_skills_for_prompt",
    "registerSkill",
    "getSkill",
    "getAllSkills",
    "getUserInvocableSkills",
    "hasSkill",
    "unregisterSkill",
    "clearSkills",
    "formatSkillsForPrompt",
    "init_bundled_skills",
    "initBundledSkills",
]
