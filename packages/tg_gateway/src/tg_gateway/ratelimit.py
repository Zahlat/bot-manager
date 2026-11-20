"""Outgoing send limits: a global bucket (~30 msg/s) plus one bucket per chat (~1 msg/s).

This module is in-memory, so it is correct for one process. A Redis-backed SendLimiter (Phase 2,
platform_kit) implements the same Protocol for several workers.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Protocol

__all__ = ["InMemorySendLimiter", "SendLimiter", "TokenBucket"]

ChatId = int | str | None
_EPSILON = 1e-9  # absorbs float drift so a sleep of exactly the reported wait always succeeds


class SendLimiter(Protocol):
    async def acquire(self, chat_id: ChatId) -> None:
        """Wait until one message to `chat_id` (None = no specific chat) may be sent."""
        ...

    def penalize(self, chat_id: ChatId, seconds: float) -> None:
        """Telegram said 429/retry_after: pause `chat_id` (or everyone, if None) for `seconds`."""
        ...


class TokenBucket:
    """`burst` tokens, refilled at `rate` per second. Pure math over an injected clock."""

    def __init__(
        self, rate: float, burst: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._tokens = burst
        self._updated = clock()
        self._blocked_until = -math.inf

    def wait_time(self, now: float | None = None) -> float:
        """Seconds until a token is available (0.0 = now). Does not consume anything."""
        now = self._clock() if now is None else now
        self._refill(now)
        if now < self._blocked_until:
            return self._blocked_until - now
        if self._tokens >= 1 - _EPSILON:
            return 0.0
        return (1 - self._tokens) / self._rate

    def take(self) -> None:
        """Consume one token; call only right after wait_time() returned 0.0."""
        self._tokens = max(0.0, self._tokens - 1)

    def try_take(self, now: float | None = None) -> float:
        """Take a token if one is available: returns 0.0, else the seconds to wait."""
        wait = self.wait_time(now)
        if wait == 0.0:
            self.take()
        return wait

    def block_for(self, seconds: float, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        self._blocked_until = max(self._blocked_until, now + seconds)

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._updated = max(self._updated, now)


class InMemorySendLimiter(SendLimiter):
    def __init__(
        self,
        *,
        global_rate: float = 30.0,
        global_burst: float = 30.0,
        chat_rate: float = 1.0,
        chat_burst: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_chats: int = 10_000,
    ) -> None:
        self.global_rate = global_rate
        self.chat_rate = chat_rate
        self._chat_burst = chat_burst
        self._clock = clock
        self._sleep = sleep
        self._max_chats = max_chats
        self._global = TokenBucket(global_rate, global_burst, clock=clock)
        # LRU-bounded so a flood of distinct chats can't grow memory without limit. An evicted
        # chat comes back with a full bucket, which only ever allows a short, legal burst.
        self._chats: OrderedDict[int | str, TokenBucket] = OrderedDict()

    @property
    def tracked_chats(self) -> tuple[int | str, ...]:
        return tuple(self._chats)

    async def acquire(self, chat_id: ChatId) -> None:
        while True:
            now = self._clock()
            chat = self._chat(chat_id) if chat_id is not None else None
            wait = max(self._global.wait_time(now), chat.wait_time(now) if chat else 0.0)
            if wait == 0.0:
                # No await between the check and the take, so concurrent tasks can't both win.
                self._global.take()
                if chat is not None:
                    chat.take()
                return
            await self._sleep(wait)

    def penalize(self, chat_id: ChatId, seconds: float) -> None:
        bucket = self._global if chat_id is None else self._chat(chat_id)
        bucket.block_for(seconds, self._clock())

    def _chat(self, chat_id: int | str) -> TokenBucket:
        bucket = self._chats.get(chat_id)
        if bucket is None:
            bucket = TokenBucket(self.chat_rate, self._chat_burst, clock=self._clock)
            self._chats[chat_id] = bucket
            if len(self._chats) > self._max_chats:
                self._chats.popitem(last=False)
        else:
            self._chats.move_to_end(chat_id)
        return bucket
