from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from forgepilot_api.sandbox.claude import ClaudeProvider
from forgepilot_api.sandbox.codex import CodexProvider
from forgepilot_api.sandbox.native import NativeProvider
from forgepilot_api.sandbox.types import ISandboxProvider, SandboxProviderType

ProviderFactory = Callable[[dict[str, Any] | None], ISandboxProvider]
SandboxState = Literal["uninitialized", "initializing", "ready", "error", "stopped"]


@dataclass(slots=True)
class SandboxPlugin:
    metadata: dict[str, Any]
    factory: ProviderFactory


@dataclass(slots=True)
class SandboxInstance:
    provider: ISandboxProvider
    state: SandboxState
    config: dict[str, Any] | None = None
    error: Exception | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return repr(value)


def _deep_equal_config(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    return _json_safe(left) == _json_safe(right)


def _legacy_metadata(provider_type: str) -> dict[str, Any]:
    defaults: dict[str, dict[str, Any]] = {
        "native": {
            "name": "Native (No Isolation)",
            "description": "Host execution",
            "isolation": "none",
            "supportedRuntimes": ["node", "python", "bash"],
            "supportsVolumeMounts": False,
            "supportsNetworking": True,
            "supportsPooling": False,
        },
        "codex": {
            "name": "Codex CLI Sandbox",
            "description": "Codex CLI based sandbox",
            "isolation": "process",
            "supportedRuntimes": ["node", "python", "bash"],
            "supportsVolumeMounts": False,
            "supportsNetworking": False,
            "supportsPooling": False,
        },
        "claude": {
            "name": "Claude Sandbox",
            "description": "Anthropic srt based sandbox",
            "isolation": "container",
            "supportedRuntimes": ["node", "python", "bash"],
            "supportsVolumeMounts": False,
            "supportsNetworking": True,
            "supportsPooling": False,
        },
    }
    base = defaults.get(provider_type, {})
    return {
        "type": provider_type,
        "name": base.get("name", provider_type),
        "description": base.get("description", f"{provider_type} sandbox provider"),
        "version": "1.0.0",
        "isolation": base.get("isolation", "none"),
        "supportedRuntimes": list(base.get("supportedRuntimes", ["node"])),
        "supportsVolumeMounts": bool(base.get("supportsVolumeMounts", False)),
        "supportsNetworking": bool(base.get("supportsNetworking", True)),
        "supportsPooling": bool(base.get("supportsPooling", False)),
    }


def _coerce_factory(factory: Callable[..., ISandboxProvider]) -> ProviderFactory:
    def _wrapped(config: dict[str, Any] | None = None) -> ISandboxProvider:
        try:
            return factory(config)
        except TypeError:
            # Legacy providers are no-arg constructors.
            return factory()

    return _wrapped


class SandboxRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, SandboxPlugin] = {}
        self._instances: dict[str, SandboxInstance] = {}

    def register(self, provider_type: SandboxProviderType, factory: Callable[..., ISandboxProvider]) -> None:
        key = str(provider_type)
        self.register_plugin(
            SandboxPlugin(
                metadata=_legacy_metadata(key),
                factory=_coerce_factory(factory),
            )
        )

    def register_plugin(self, plugin: SandboxPlugin) -> None:
        provider_type = str(plugin.metadata.get("type") or "")
        if not provider_type:
            raise ValueError("sandbox plugin metadata.type is required")
        self._plugins[provider_type] = plugin

    def get(self, provider_type: SandboxProviderType) -> ProviderFactory | None:
        return self.get_factory(str(provider_type))

    def get_factory(self, provider_type: str) -> ProviderFactory | None:
        plugin = self._plugins.get(str(provider_type))
        return plugin.factory if plugin else None

    def get_metadata(self) -> list[dict[str, Any]]:
        return self.get_all_sandbox_metadata()

    def get_all_metadata(self) -> list[dict[str, Any]]:
        return self.get_all_sandbox_metadata()

    def get_all_sandbox_metadata(self) -> list[dict[str, Any]]:
        return [dict(plugin.metadata) for plugin in self._plugins.values()]

    def get_sandbox_metadata(self, provider_type: str) -> dict[str, Any] | None:
        plugin = self._plugins.get(str(provider_type))
        return dict(plugin.metadata) if plugin else None

    def create(
        self,
        config_or_type: SandboxProviderType | dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> ISandboxProvider:
        if isinstance(config_or_type, dict):
            provider_type = str(config_or_type.get("type") or "")
            cfg = config_or_type
        else:
            provider_type = str(config_or_type)
            cfg = config

        plugin = self._plugins.get(provider_type)
        if not plugin:
            available = ", ".join(self.get_registered())
            raise ValueError(f"Sandbox provider not registered: {provider_type}. Available: {available}")
        return plugin.factory(cfg)

    async def get_instance(
        self,
        provider_type: SandboxProviderType,
        config: dict[str, Any] | None = None,
    ) -> ISandboxProvider:
        key = str(provider_type)
        instance = self._instances.get(key)

        if instance and instance.state == "ready":
            if _deep_equal_config(instance.config, config):
                instance.last_used_at = datetime.now(timezone.utc)
                return instance.provider
            await self._shutdown_instance(key, instance)
            instance = None

        if instance and instance.state == "error":
            self._instances.pop(key, None)
            instance = None

        provider = self.create(key, config)
        instance = SandboxInstance(
            provider=provider,
            state="initializing",
            config=config,
            created_at=datetime.now(timezone.utc),
            last_used_at=datetime.now(timezone.utc),
        )
        self._instances[key] = instance
        try:
            runtime_cfg = config.get("config") if isinstance(config, dict) else None
            await provider.init(runtime_cfg)
            instance.state = "ready"
            return provider
        except Exception as exc:
            instance.state = "error"
            instance.error = exc if isinstance(exc, Exception) else Exception(str(exc))
            raise

    async def get_available(self) -> list[str]:
        available: list[str] = []
        for provider_type, plugin in self._plugins.items():
            provider: ISandboxProvider | None = None
            try:
                provider = plugin.factory(None)
                if await provider.is_available():
                    available.append(provider_type)
            except Exception:
                continue
            finally:
                if provider is not None:
                    try:
                        await provider.shutdown()
                    except Exception:
                        pass
        return available

    def get_registered(self) -> list[str]:
        return list(self._plugins.keys())

    async def stop_all(self) -> None:
        for key, instance in list(self._instances.items()):
            await self._shutdown_instance(key, instance)
        self._instances.clear()

    async def _shutdown_instance(self, provider_type: str, instance: SandboxInstance) -> None:
        try:
            await instance.provider.stop()
        except Exception:
            try:
                await instance.provider.shutdown()
            except Exception:
                pass
        finally:
            instance.state = "stopped"
            self._instances.pop(provider_type, None)

    def get_by_isolation(self, isolation: str) -> list[str]:
        out: list[str] = []
        for metadata in self.get_all_sandbox_metadata():
            if str(metadata.get("isolation") or "") == isolation:
                out.append(str(metadata.get("type") or ""))
        return out

    def get_by_runtime(self, runtime: str) -> list[str]:
        out: list[str] = []
        for metadata in self.get_all_sandbox_metadata():
            runtimes = metadata.get("supportedRuntimes") or []
            if isinstance(runtimes, list) and runtime in runtimes:
                out.append(str(metadata.get("type") or ""))
        return out

    async def get_best_available(self) -> str | None:
        priority = ["codex", "claude", "docker", "native"]
        available = await self.get_available()
        for item in priority:
            if item in available:
                return item
        return available[0] if available else None


_registry: SandboxRegistry | None = None


def get_sandbox_registry() -> SandboxRegistry:
    global _registry
    if _registry is None:
        _registry = SandboxRegistry()
        _registry.register("native", NativeProvider)
        _registry.register("codex", CodexProvider)
        _registry.register("claude", ClaudeProvider)
    return _registry


def register_sandbox_provider(
    provider_type: SandboxProviderType,
    factory: Callable[..., ISandboxProvider],
) -> None:
    get_sandbox_registry().register(provider_type, factory)


def create_sandbox_provider(config: dict[str, Any]) -> ISandboxProvider:
    return get_sandbox_registry().create(config)


async def get_sandbox_provider(
    provider_type: SandboxProviderType,
    config: dict[str, Any] | None = None,
) -> ISandboxProvider:
    return await get_sandbox_registry().get_instance(provider_type, config)


async def get_available_sandbox_providers() -> list[str]:
    return await get_sandbox_registry().get_available()


async def stop_all_sandbox_providers() -> None:
    await get_sandbox_registry().stop_all()
