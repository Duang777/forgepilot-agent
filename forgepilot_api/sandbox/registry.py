from __future__ import annotations

from typing import Callable

from forgepilot_api.sandbox.claude import ClaudeProvider
from forgepilot_api.sandbox.codex import CodexProvider
from forgepilot_api.sandbox.native import NativeProvider
from forgepilot_api.sandbox.types import ISandboxProvider, SandboxProviderType

ProviderFactory = Callable[[], ISandboxProvider]


class SandboxRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._instances: dict[str, ISandboxProvider] = {}

    def register(self, provider_type: SandboxProviderType, factory: ProviderFactory) -> None:
        self._factories[str(provider_type)] = factory

    def get_metadata(self) -> list[dict[str, str]]:
        return [
            {"type": "native", "name": "Native (No Isolation)", "description": "Host execution"},
            {"type": "codex", "name": "Codex CLI Sandbox", "description": "Codex CLI based sandbox"},
            {"type": "claude", "name": "Claude Sandbox", "description": "Anthropic srt based sandbox"},
        ]

    async def get_available(self) -> list[str]:
        available: list[str] = []
        for provider_type in self._factories:
            provider = await self.get_instance(provider_type)
            if await provider.is_available():
                available.append(provider_type)
        return available

    async def get_instance(self, provider_type: SandboxProviderType) -> ISandboxProvider:
        key = str(provider_type)
        if key in self._instances:
            return self._instances[key]
        instance = self.create(key)
        await instance.init()
        self._instances[key] = instance
        return instance

    def create(self, provider_type: SandboxProviderType) -> ISandboxProvider:
        key = str(provider_type)
        if key not in self._factories:
            raise ValueError(f"Sandbox provider not registered: {provider_type}")
        return self._factories[key]()

    async def stop_all(self) -> None:
        for provider in self._instances.values():
            await provider.shutdown()
        self._instances = {}


_registry: SandboxRegistry | None = None


def get_sandbox_registry() -> SandboxRegistry:
    global _registry
    if _registry is None:
        _registry = SandboxRegistry()
        _registry.register("native", NativeProvider)
        _registry.register("codex", CodexProvider)
        _registry.register("claude", ClaudeProvider)
    return _registry

