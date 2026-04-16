from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class FileState:
    content: str
    timestamp: float
    offset: int | None = None
    limit: int | None = None
    is_partial_view: bool | None = None


class FileStateCache:
    def __init__(self, max_entries: int = 100, max_size_bytes: int = 25 * 1024 * 1024) -> None:
        self._cache: dict[str, FileState] = {}
        self._order: list[str] = []
        self._max_entries = max(1, int(max_entries))
        self._max_size_bytes = max(1, int(max_size_bytes))
        self._current_size_bytes = 0

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        return str(Path(file_path).expanduser().resolve())

    @staticmethod
    def _entry_size(content: str) -> int:
        return len(content.encode("utf-8"))

    def _touch(self, key: str) -> None:
        try:
            self._order.remove(key)
        except ValueError:
            pass
        self._order.append(key)

    def _pop_lru(self) -> None:
        if not self._order:
            return
        lru_key = self._order.pop(0)
        entry = self._cache.pop(lru_key, None)
        if entry is not None:
            self._current_size_bytes -= self._entry_size(entry.content)

    def get(self, file_path: str) -> FileState | None:
        key = self._normalize_path(file_path)
        entry = self._cache.get(key)
        if entry is None:
            return None
        self._touch(key)
        return entry

    def set(self, file_path: str, state: FileState) -> None:
        key = self._normalize_path(file_path)
        if key in self._cache:
            previous = self._cache[key]
            self._current_size_bytes -= self._entry_size(previous.content)
            self._cache.pop(key, None)
            try:
                self._order.remove(key)
            except ValueError:
                pass

        new_size = self._entry_size(state.content)
        while (
            (len(self._cache) >= self._max_entries or self._current_size_bytes + new_size > self._max_size_bytes)
            and self._cache
        ):
            self._pop_lru()

        self._cache[key] = state
        self._order.append(key)
        self._current_size_bytes += new_size

    def delete(self, file_path: str) -> bool:
        key = self._normalize_path(file_path)
        entry = self._cache.pop(key, None)
        if entry is None:
            return False
        self._current_size_bytes -= self._entry_size(entry.content)
        try:
            self._order.remove(key)
        except ValueError:
            pass
        return True

    def clear(self) -> None:
        self._cache.clear()
        self._order.clear()
        self._current_size_bytes = 0

    @property
    def size(self) -> int:
        return len(self._cache)

    def keys(self) -> list[str]:
        return list(self._order)

    def clone(self) -> FileStateCache:
        clone = FileStateCache(self._max_entries, self._max_size_bytes)
        for key in self._order:
            entry = self._cache[key]
            clone._cache[key] = FileState(
                content=entry.content,
                timestamp=entry.timestamp,
                offset=entry.offset,
                limit=entry.limit,
                is_partial_view=entry.is_partial_view,
            )
            clone._order.append(key)
            clone._current_size_bytes += clone._entry_size(entry.content)
        return clone


def create_file_state_cache(max_entries: int = 100, max_size_bytes: int = 25 * 1024 * 1024) -> FileStateCache:
    return FileStateCache(max_entries=max_entries, max_size_bytes=max_size_bytes)


def createFileStateCache(maxEntries: int = 100, maxSizeBytes: int = 25 * 1024 * 1024) -> FileStateCache:
    return create_file_state_cache(max_entries=maxEntries, max_size_bytes=maxSizeBytes)
