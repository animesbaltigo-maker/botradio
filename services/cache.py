from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class _CacheEntry(Generic[T]):
    value: T
    expires_at: float


class AsyncTTLCache(Generic[T]):
    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry[T]] = {}
        self._inflight: dict[str, asyncio.Task[T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            return self._get_locked(key)

    async def set(self, key: str, value: T, ttl_seconds: float) -> T:
        expires_at = time.monotonic() + max(ttl_seconds, 0.0)
        async with self._lock:
            self._entries[key] = _CacheEntry(value=value, expires_at=expires_at)
        return value

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._entries.pop(key, None)
            self._inflight.pop(key, None)

    async def peek(self, key: str) -> T | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            return entry.value

    async def get_or_create(
        self,
        key: str,
        *,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        async with self._lock:
            cached = self._get_locked(key)
            if cached is not None:
                return cached

            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(loader())
                self._inflight[key] = task
                creator = True
            else:
                creator = False

        try:
            value = await task
        except Exception:
            if creator:
                async with self._lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)
            raise

        if creator:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)
                self._entries[key] = _CacheEntry(
                    value=value,
                    expires_at=time.monotonic() + max(ttl_seconds, 0.0),
                )

        return value

    def _get_locked(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        return entry.value

