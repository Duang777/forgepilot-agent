from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from forgepilot_api.sandbox.registry import get_sandbox_registry
from forgepilot_api.services.codex_config_service import load_codex_runtime_config
from forgepilot_api.storage.repositories import read_settings, write_setting

DEFAULT_SANDBOX_PROVIDER = "codex"
DEFAULT_AGENT_PROVIDER = "codeany"


class _RegistryProtocol(Protocol):
    async def get_available(self) -> list[str]:
        ...

    def get_all_metadata(self) -> list[dict[str, Any]]:
        ...

    async def get_instance(self, provider_type: str, config: dict[str, Any] | None = None) -> Any:
        ...

    async def stop_all(self) -> None:
        ...


@dataclass(slots=True)
class _ProviderSelection:
    category: str
    type: str
    config: dict[str, Any] | None = None


@dataclass(slots=True)
class _ProviderState:
    sandbox_type: str = DEFAULT_SANDBOX_PROVIDER
    sandbox_config: dict[str, Any] = field(default_factory=dict)
    agent_type: str = DEFAULT_AGENT_PROVIDER
    agent_config: dict[str, Any] = field(default_factory=dict)
    default_provider: str = ""
    default_model: str = ""


@dataclass(slots=True)
class _ProviderEvent:
    type: str
    provider_type: str
    timestamp: datetime
    data: dict[str, Any] = field(default_factory=dict)


ProviderEventListener = Callable[[_ProviderEvent], None]


class _InProcessAgentProvider:
    def __init__(self, provider_type: str, config: dict[str, Any] | None = None) -> None:
        self.type = provider_type
        self.config = config or {}

    async def shutdown(self) -> None:
        return None


class _AgentRegistryAdapter:
    def __init__(self, metadata: list[dict[str, Any]]) -> None:
        self._metadata = metadata

    async def get_available(self) -> list[str]:
        return [str(item.get("type") or "") for item in self._metadata if item.get("type")]

    def get_all_metadata(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._metadata]

    async def get_instance(self, provider_type: str, config: dict[str, Any] | None = None) -> Any:
        known = {str(item.get("type") or "") for item in self._metadata}
        if provider_type not in known:
            raise ValueError(f"Unknown agent provider: {provider_type}")
        return _InProcessAgentProvider(provider_type, config)

    async def stop_all(self) -> None:
        return None


class _SandboxRegistryAdapter:
    def __init__(self) -> None:
        self._registry = get_sandbox_registry()

    async def get_available(self) -> list[str]:
        return await self._registry.get_available()

    def get_all_metadata(self) -> list[dict[str, Any]]:
        return self._registry.get_all_sandbox_metadata()

    async def get_instance(self, provider_type: str, config: dict[str, Any] | None = None) -> Any:
        return await self._registry.get_instance(provider_type, config)

    async def stop_all(self) -> None:
        await self._registry.stop_all()


AGENT_METADATA: list[dict[str, Any]] = [
    {"type": "codeany", "name": "CodeAny Agent", "description": "In-process Open Agent SDK runtime"},
    {"type": "custom", "name": "Custom Agent", "description": "Custom external runtime"},
]


def _with_status(
    metadata: list[dict[str, Any]],
    *,
    current_type: str,
    available: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in metadata:
        ptype = str(item.get("type") or "")
        rows.append(
            {
                "type": ptype,
                "name": str(item.get("name") or ptype),
                "description": str(item.get("description") or ""),
                "available": ptype in available,
                "current": ptype == current_type,
            }
        )
    return rows


class ProviderManager:
    def __init__(self) -> None:
        self._state = _ProviderState()
        self._registries: dict[str, _RegistryProtocol] = {}
        self._active_providers: dict[str, Any] = {}
        self._listeners: set[ProviderEventListener] = set()
        self._initialized = False
        self._agent_config_seeded_from_codex = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            self.register_registry("sandbox", _SandboxRegistryAdapter())
            self.register_registry("agent", _AgentRegistryAdapter(AGENT_METADATA))
            await self._load_from_settings_locked()

            if not self._state.sandbox_type:
                self._state.sandbox_type = str(os.getenv("SANDBOX_PROVIDER") or DEFAULT_SANDBOX_PROVIDER)
            if not self._state.agent_type:
                self._state.agent_type = str(os.getenv("AGENT_PROVIDER") or DEFAULT_AGENT_PROVIDER)
            self._initialized = True

    async def init(self) -> None:
        await self.initialize()

    async def _load_from_settings_locked(self) -> None:
        settings = await read_settings()
        self._state.sandbox_type = str(
            settings.get("sandboxProvider")
            or os.getenv("SANDBOX_PROVIDER")
            or self._state.sandbox_type
        )
        self._state.sandbox_config = dict(settings.get("sandboxConfig") or {})
        self._state.agent_type = str(
            settings.get("agentProvider")
            or os.getenv("AGENT_PROVIDER")
            or self._state.agent_type
        )
        self._state.agent_config = dict(settings.get("agentConfig") or {})
        self._state.default_provider = str(settings.get("defaultProvider") or self._state.default_provider)
        self._state.default_model = str(settings.get("defaultModel") or self._state.default_model)
        self._agent_config_seeded_from_codex = False

        codex_cfg = load_codex_runtime_config()
        if not self._state.agent_config:
            seeded: dict[str, Any] = {}
            if codex_cfg.get("apiKey"):
                seeded["apiKey"] = str(codex_cfg["apiKey"])
            if codex_cfg.get("baseUrl"):
                seeded["baseUrl"] = str(codex_cfg["baseUrl"])
            if codex_cfg.get("model"):
                seeded["model"] = str(codex_cfg["model"])
            if codex_cfg.get("apiType"):
                seeded["apiType"] = str(codex_cfg["apiType"])
            if seeded:
                self._state.agent_config = seeded
                self._agent_config_seeded_from_codex = True
        if not self._state.default_model and codex_cfg.get("model"):
            self._state.default_model = str(codex_cfg["model"])

    def register_registry(self, category: str, registry: _RegistryProtocol) -> None:
        self._registries[str(category)] = registry

    def registerRegistry(self, category: str, registry: _RegistryProtocol) -> None:
        self.register_registry(category, registry)

    def on(self, listener: ProviderEventListener) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def off(self, listener: ProviderEventListener) -> None:
        self._listeners.discard(listener)

    def _emit(self, event: _ProviderEvent) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                continue

    async def _ensure_loaded(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def _persist(self) -> None:
        await write_setting("sandboxProvider", self._state.sandbox_type)
        await write_setting("sandboxConfig", self._state.sandbox_config)
        await write_setting("agentProvider", self._state.agent_type)
        await write_setting("agentConfig", self._state.agent_config)
        await write_setting("defaultProvider", self._state.default_provider)
        await write_setting("defaultModel", self._state.default_model)

    async def get_provider(self, category: str) -> Any | None:
        await self._ensure_loaded()
        registry = self._registries.get(category)
        if registry is None:
            return None
        selection = self._selection_for(category)
        if selection is None:
            available = await registry.get_available()
            if not available:
                return None
            return await registry.get_instance(available[0], None)
        return await registry.get_instance(selection.type, selection.config)

    async def getProvider(self, category: str) -> Any | None:
        return await self.get_provider(category)

    async def get_sandbox_provider(self) -> Any | None:
        return await self.get_provider("sandbox")

    async def getSandboxProvider(self) -> Any | None:
        return await self.get_sandbox_provider()

    async def get_agent_provider(self) -> Any | None:
        return await self.get_provider("agent")

    async def getAgentProvider(self) -> Any | None:
        return await self.get_agent_provider()

    def _selection_for(self, category: str) -> _ProviderSelection | None:
        if category == "sandbox":
            return _ProviderSelection("sandbox", self._state.sandbox_type, dict(self._state.sandbox_config))
        if category == "agent":
            return _ProviderSelection("agent", self._state.agent_type, dict(self._state.agent_config))
        return None

    async def switch_provider(self, category: str, provider_type: str, config: dict[str, Any] | None = None) -> None:
        await self._ensure_loaded()
        registry = self._registries.get(category)
        if registry is None:
            raise ValueError(f"No registry for category: {category}")

        current = self._active_providers.get(category)
        if current is not None:
            shutdown = getattr(current, "shutdown", None)
            if callable(shutdown):
                maybe = shutdown()
                if asyncio.iscoroutine(maybe) or hasattr(maybe, "__await__"):
                    await maybe
            self._active_providers.pop(category, None)

        provider = await registry.get_instance(provider_type, config)
        self._active_providers[category] = provider

        if category == "sandbox":
            self._state.sandbox_type = provider_type
            self._state.sandbox_config = dict(config or {})
        elif category == "agent":
            self._state.agent_type = provider_type
            self._state.agent_config = dict(config or {})
            self._agent_config_seeded_from_codex = False
        else:
            raise ValueError(f"Unsupported provider category: {category}")

        await self._persist()
        self._emit(
            _ProviderEvent(
                type="provider:switched",
                provider_type=provider_type,
                timestamp=datetime.now(timezone.utc),
                data={"category": category},
            )
        )

    async def switchProvider(self, category: str, provider_type: str, config: dict[str, Any] | None = None) -> None:
        await self.switch_provider(category, provider_type, config)

    async def switch_sandbox_provider(self, provider_type: str, config: dict[str, Any] | None = None) -> None:
        await self.switch_provider("sandbox", provider_type, config)

    async def switchSandboxProvider(self, provider_type: str, config: dict[str, Any] | None = None) -> None:
        await self.switch_sandbox_provider(provider_type, config)

    async def switch_agent_provider(self, provider_type: str, config: dict[str, Any] | None = None) -> None:
        await self.switch_provider("agent", provider_type, config)

    async def switchAgentProvider(self, provider_type: str, config: dict[str, Any] | None = None) -> None:
        await self.switch_agent_provider(provider_type, config)

    async def get_available_sandbox_providers(self) -> list[str]:
        await self._ensure_loaded()
        registry = self._registries.get("sandbox")
        return await registry.get_available() if registry else []

    async def getAvailableSandboxProviders(self) -> list[str]:
        return await self.get_available_sandbox_providers()

    async def get_available_agent_providers(self) -> list[str]:
        await self._ensure_loaded()
        registry = self._registries.get("agent")
        return await registry.get_available() if registry else []

    async def getAvailableAgentProviders(self) -> list[str]:
        return await self.get_available_agent_providers()

    async def get_sandbox_providers(self) -> dict[str, Any]:
        await self._ensure_loaded()
        registry = self._registries.get("sandbox")
        metadata = registry.get_all_metadata() if registry else []
        available = await self.get_available_sandbox_providers()
        providers = _with_status(metadata, current_type=self._state.sandbox_type, available=available)
        return {"providers": providers, "current": self._state.sandbox_type}

    async def getSandboxProvidersMetadata(self) -> list[dict[str, Any]]:
        await self._ensure_loaded()
        registry = self._registries.get("sandbox")
        return registry.get_all_metadata() if registry else []

    async def get_agent_providers(self) -> dict[str, Any]:
        await self._ensure_loaded()
        registry = self._registries.get("agent")
        metadata = registry.get_all_metadata() if registry else []
        available = await self.get_available_agent_providers()
        providers = _with_status(metadata, current_type=self._state.agent_type, available=available)
        return {"providers": providers, "current": self._state.agent_type}

    async def getAgentProvidersMetadata(self) -> list[dict[str, Any]]:
        await self._ensure_loaded()
        registry = self._registries.get("agent")
        return registry.get_all_metadata() if registry else []

    async def sync_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_loaded()
        if body.get("sandboxProvider"):
            await self.switch_sandbox_provider(str(body["sandboxProvider"]), body.get("sandboxConfig") or {})
        if body.get("agentProvider"):
            await self.switch_agent_provider(str(body["agentProvider"]), body.get("agentConfig") or {})
        if "defaultProvider" in body:
            self._state.default_provider = str(body.get("defaultProvider") or "")
        if "defaultModel" in body:
            self._state.default_model = str(body.get("defaultModel") or "")
        await self._persist()
        return self.get_config()

    async def syncSettings(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.sync_settings(body)

    def update_from_settings(self, settings: dict[str, Any]) -> None:
        if settings.get("sandboxProvider"):
            self._state.sandbox_type = str(settings["sandboxProvider"])
            self._state.sandbox_config = dict(settings.get("sandboxConfig") or {})
        if settings.get("agentProvider"):
            self._state.agent_type = str(settings["agentProvider"])
            self._state.agent_config = dict(settings.get("agentConfig") or {})
            self._agent_config_seeded_from_codex = False
        if "defaultProvider" in settings:
            self._state.default_provider = str(settings.get("defaultProvider") or "")
        if "defaultModel" in settings:
            self._state.default_model = str(settings.get("defaultModel") or "")

    def updateFromSettings(self, settings: dict[str, Any]) -> None:
        self.update_from_settings(settings)

    def get_config(self) -> dict[str, Any]:
        if self._state.agent_config:
            source = "codex" if self._agent_config_seeded_from_codex else "settings"
        else:
            source = "empty"
        return {
            "sandbox": {
                "category": "sandbox",
                "type": self._state.sandbox_type,
                "config": dict(self._state.sandbox_config),
            },
            "agent": {
                "category": "agent",
                "type": self._state.agent_type,
                "config": dict(self._state.agent_config),
            },
            "agentConfigSource": source,
            "defaultProvider": self._state.default_provider,
            "defaultModel": self._state.default_model,
        }

    def getConfig(self) -> dict[str, Any]:
        return self.get_config()

    async def shutdown(self) -> None:
        for category, provider in list(self._active_providers.items()):
            try:
                shutdown = getattr(provider, "shutdown", None)
                if callable(shutdown):
                    maybe = shutdown()
                    if asyncio.iscoroutine(maybe) or hasattr(maybe, "__await__"):
                        await maybe
            except Exception:
                pass
            finally:
                self._active_providers.pop(category, None)

        for registry in self._registries.values():
            try:
                await registry.stop_all()
            except Exception:
                continue

    async def stop(self) -> None:
        await self.shutdown()


_provider_manager: ProviderManager | None = None


def get_provider_manager() -> ProviderManager:
    global _provider_manager
    if _provider_manager is None:
        _provider_manager = ProviderManager()
    return _provider_manager


def getProviderManager() -> ProviderManager:
    return get_provider_manager()


async def init_provider_manager() -> ProviderManager:
    manager = get_provider_manager()
    await manager.initialize()
    return manager


async def initProviderManager() -> ProviderManager:
    return await init_provider_manager()


async def shutdown_provider_manager() -> None:
    global _provider_manager
    if _provider_manager is not None:
        await _provider_manager.shutdown()


async def shutdownProviderManager() -> None:
    await shutdown_provider_manager()


async def ensure_loaded() -> None:
    await get_provider_manager().initialize()


async def get_available_sandbox_providers() -> list[str]:
    return await get_provider_manager().get_available_sandbox_providers()


async def get_available_agent_providers() -> list[str]:
    return await get_provider_manager().get_available_agent_providers()


async def get_sandbox_providers() -> dict[str, Any]:
    return await get_provider_manager().get_sandbox_providers()


async def get_agent_providers() -> dict[str, Any]:
    return await get_provider_manager().get_agent_providers()


async def switch_sandbox_provider(provider_type: str, config: dict[str, Any] | None = None) -> None:
    await get_provider_manager().switch_sandbox_provider(provider_type, config)


async def switch_agent_provider(provider_type: str, config: dict[str, Any] | None = None) -> None:
    await get_provider_manager().switch_agent_provider(provider_type, config)


async def sync_settings(body: dict[str, Any]) -> dict[str, Any]:
    return await get_provider_manager().sync_settings(body)


async def get_config() -> dict[str, Any]:
    await ensure_loaded()
    return get_provider_manager().get_config()
