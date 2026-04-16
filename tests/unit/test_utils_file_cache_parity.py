from __future__ import annotations

from forgepilot_sdk.utils.file_cache import FileState, create_file_state_cache


def test_file_cache_get_set_and_lru_eviction_by_entries() -> None:
    cache = create_file_state_cache(max_entries=2, max_size_bytes=1024 * 1024)
    cache.set("a.txt", FileState(content="A", timestamp=1))
    cache.set("b.txt", FileState(content="B", timestamp=2))
    assert cache.size == 2
    assert cache.get("a.txt") is not None  # Touch a -> b becomes LRU
    cache.set("c.txt", FileState(content="C", timestamp=3))
    assert cache.get("b.txt") is None
    assert cache.get("a.txt") is not None
    assert cache.get("c.txt") is not None


def test_file_cache_evicts_by_total_size() -> None:
    cache = create_file_state_cache(max_entries=10, max_size_bytes=6)
    cache.set("a.txt", FileState(content="aaaa", timestamp=1))
    cache.set("b.txt", FileState(content="bb", timestamp=2))
    assert cache.get("a.txt") is not None
    cache.set("c.txt", FileState(content="cccc", timestamp=3))
    assert cache.get("a.txt") is None
    assert cache.get("b.txt") is None
    assert cache.get("c.txt") is not None


def test_file_cache_delete_clear_clone() -> None:
    cache = create_file_state_cache(max_entries=3, max_size_bytes=1024 * 1024)
    cache.set("x.txt", FileState(content="x", timestamp=1, offset=0, limit=10, is_partial_view=True))
    cache.set("y.txt", FileState(content="y", timestamp=2))
    assert cache.delete("x.txt") is True
    assert cache.delete("x.txt") is False

    clone = cache.clone()
    assert clone.size == cache.size
    assert clone.keys() == cache.keys()
    cache.clear()
    assert cache.size == 0
    assert clone.size == 1
