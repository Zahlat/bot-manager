"""Slice 3: token buckets that keep sends under Telegram's limits (~30/s global, ~1/s per chat)."""

from __future__ import annotations

import asyncio

import pytest

from tg_gateway.ratelimit import InMemorySendLimiter, TokenBucket

pytestmark = pytest.mark.unit


class FakeClock:
    """A monotonic clock that only moves when someone sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)  # let other tasks run, like a real sleep would


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def limiter(clock: FakeClock, **kwargs: float) -> InMemorySendLimiter:
    settings: dict[str, float] = {
        "global_rate": 1000.0,
        "global_burst": 1000.0,
        "chat_rate": 1000.0,
        "chat_burst": 1000.0,
    }
    settings.update(kwargs)
    return InMemorySendLimiter(clock=clock, sleep=clock.sleep, **settings)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- TokenBucket


def test_bucket_allows_burst_then_reports_wait(clock: FakeClock) -> None:
    bucket = TokenBucket(rate=1.0, burst=3.0, clock=clock)

    assert [bucket.try_take() for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.try_take() == pytest.approx(1.0)


def test_bucket_refills_at_rate_and_caps_at_burst(clock: FakeClock) -> None:
    bucket = TokenBucket(rate=2.0, burst=2.0, clock=clock)
    bucket.try_take()
    bucket.try_take()

    clock.now = 0.25
    assert bucket.try_take() == pytest.approx(0.25)  # half a token so far; needs 0.25 s more
    clock.now = 0.5
    assert bucket.try_take() == 0.0

    clock.now = 100.0  # a long idle time never banks more than `burst`
    assert [bucket.try_take() for _ in range(2)] == [0.0, 0.0]
    assert bucket.try_take() > 0.0


def test_failed_take_does_not_consume(clock: FakeClock) -> None:
    bucket = TokenBucket(rate=1.0, burst=1.0, clock=clock)
    bucket.try_take()

    assert bucket.try_take() == pytest.approx(1.0)
    assert bucket.try_take() == pytest.approx(1.0)  # asking again doesn't push the wait out
    clock.now = 1.0
    assert bucket.try_take() == 0.0


def test_bucket_block_delays_until_deadline(clock: FakeClock) -> None:
    bucket = TokenBucket(rate=10.0, burst=10.0, clock=clock)

    bucket.block_for(5.0)

    assert bucket.try_take() == pytest.approx(5.0)
    clock.now = 5.0
    assert bucket.try_take() == 0.0


@pytest.mark.parametrize(("rate", "burst"), [(0.0, 1.0), (-1.0, 1.0), (1.0, 0.5)])
def test_bucket_rejects_bad_settings(clock: FakeClock, rate: float, burst: float) -> None:
    with pytest.raises(ValueError, match=r"."):
        TokenBucket(rate=rate, burst=burst, clock=clock)


# --------------------------------------------------------------------------- limiter


async def test_per_chat_limit_does_not_slow_other_chats(clock: FakeClock) -> None:
    lim = limiter(clock, chat_rate=1.0, chat_burst=1.0)

    await lim.acquire(1)
    await lim.acquire(2)
    assert clock.now == 0.0  # different chats: no waiting

    await lim.acquire(1)
    assert clock.now == pytest.approx(1.0)  # same chat again: waits for a token


async def test_global_limit_applies_across_chats(clock: FakeClock) -> None:
    lim = limiter(clock, global_rate=2.0, global_burst=2.0)

    for chat in (1, 2, 3):
        await lim.acquire(chat)

    assert clock.now == pytest.approx(0.5)


async def test_acquire_without_chat_only_uses_global(clock: FakeClock) -> None:
    lim = limiter(clock, chat_rate=1.0, chat_burst=1.0)

    for _ in range(5):
        await lim.acquire(None)

    assert clock.now == 0.0
    assert lim.tracked_chats == ()


async def test_global_penalty_blocks_everyone(clock: FakeClock) -> None:
    lim = limiter(clock)

    lim.penalize(None, 5.0)
    await lim.acquire(7)

    assert clock.now == pytest.approx(5.0)


async def test_chat_penalty_blocks_only_that_chat(clock: FakeClock) -> None:
    lim = limiter(clock)

    lim.penalize(1, 5.0)
    await lim.acquire(2)
    assert clock.now == 0.0
    await lim.acquire(1)
    assert clock.now == pytest.approx(5.0)


async def test_concurrent_acquires_never_exceed_the_rate(clock: FakeClock) -> None:
    lim = limiter(clock, chat_rate=1.0, chat_burst=3.0)
    granted: list[float] = []

    async def send() -> None:
        await lim.acquire(42)
        granted.append(clock.now)

    await asyncio.gather(*(send() for _ in range(8)))

    granted.sort()
    assert len(granted) == 8
    # With burst 3 and 1 token/s, the i-th grant (0-based) can't happen before i - 2 seconds.
    for i, at in enumerate(granted):
        assert at >= max(0, i - 2) - 1e-9


async def test_idle_chats_are_evicted_least_recently_used_first(clock: FakeClock) -> None:
    lim = InMemorySendLimiter(clock=clock, sleep=clock.sleep, max_chats=2)

    await lim.acquire(1)
    await lim.acquire(2)
    await lim.acquire(1)  # 1 is now the most recently used
    await lim.acquire(3)

    assert set(lim.tracked_chats) == {1, 3}


def test_default_limits_match_telegram() -> None:
    lim = InMemorySendLimiter()

    assert lim.global_rate == 30.0
    assert lim.chat_rate == 1.0
