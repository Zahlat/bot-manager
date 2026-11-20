"""When to retry a failed Bot API call, and for how long to wait. Pure: no I/O, no clock, no RNG."""

from __future__ import annotations

from dataclasses import dataclass

from tg_gateway.errors import (
    TelegramError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

__all__ = ["RetryPolicy"]

_MAX_EXPONENT = 32  # 0.5 * 2**32 is far above any sane cap; avoids float overflow on huge attempts


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4  # total attempts, including the first one
    max_retry_after: float = 60.0  # longer waits are handed back to the caller to reschedule
    retry_after_jitter: float = 0.5  # adds U[0, jitter) so many workers don't retry in lockstep
    backoff_base: float = 0.5
    backoff_cap: float = 8.0

    def decide(  # noqa: PLR0911  # a flat decision table reads better than nested branches
        self, *, attempt: int, error: TelegramError, idempotent: bool, rand: float
    ) -> float | None:
        """Seconds to sleep before the next attempt, or None to raise `error` now.

        `attempt` is the number of the attempt that just failed (1-based); `rand` is in [0, 1).
        """
        if attempt >= self.max_attempts:
            return None

        if isinstance(error, TelegramRetryAfter):
            # Telegram rejected the request without processing it: safe for every method.
            if error.retry_after is None:
                return self.backoff(attempt, rand)
            if error.retry_after > self.max_retry_after:
                return None
            return error.retry_after + rand * self.retry_after_jitter

        if isinstance(error, TelegramNetworkError):
            # Never sent -> cannot duplicate anything. Sent -> only if repeating is harmless.
            return self.backoff(attempt, rand) if (idempotent or not error.sent) else None

        if isinstance(error, TelegramServerError) and idempotent:
            return self.backoff(attempt, rand)

        return None

    def backoff(self, attempt: int, rand: float) -> float:
        """Exponential backoff with full jitter: rand * min(cap, base * 2**(attempt - 1))."""
        exponent = min(attempt - 1, _MAX_EXPONENT)
        return rand * min(self.backoff_cap, self.backoff_base * 2.0**exponent)
