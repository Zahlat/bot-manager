"""Group B: RetryPolicy.decide() is pure: (attempt, error, idempotent, rand) -> delay | None."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tg_gateway.errors import (
    TelegramDecodeError,
    TelegramError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    map_error,
)
from tg_gateway.retry import RetryPolicy

pytestmark = pytest.mark.unit

POLICY = RetryPolicy()


# 8
@given(
    attempt=st.integers(min_value=1, max_value=5_000), rand=st.floats(0.0, 1.0, exclude_max=True)
)
def test_backoff_delay_always_within_zero_and_cap(attempt: int, rand: float) -> None:
    policy = RetryPolicy(max_attempts=10_000)
    error = TelegramServerError("getMe", 500, "Internal Server Error")

    delay = policy.decide(attempt=attempt, error=error, idempotent=True, rand=rand)

    assert delay is not None
    assert 0.0 <= delay <= policy.backoff_cap


# 9
@pytest.mark.parametrize("code", [400, 401, 403, 404, 409])
def test_never_retries_4xx_other_than_429(code: int) -> None:
    error = map_error("getMe", code, {"ok": False, "error_code": code, "description": "x"})

    assert POLICY.decide(attempt=1, error=error, idempotent=True, rand=0.5) is None


def test_decode_error_is_never_retried() -> None:
    error = TelegramDecodeError("getMe", "response was not JSON")

    assert POLICY.decide(attempt=1, error=error, idempotent=True, rand=0.5) is None


# 10
def test_non_idempotent_not_retried_after_send_but_retried_before_send() -> None:
    not_sent = TelegramNetworkError("sendMessage", sent=False, reason="ConnectError")
    sent = TelegramNetworkError("sendMessage", sent=True, reason="ReadTimeout")
    server = TelegramServerError("sendMessage", 502, "Bad Gateway")

    assert POLICY.decide(attempt=1, error=not_sent, idempotent=False, rand=0.5) is not None
    assert POLICY.decide(attempt=1, error=sent, idempotent=False, rand=0.5) is None
    assert POLICY.decide(attempt=1, error=server, idempotent=False, rand=0.5) is None
    # the same errors ARE retried when the method is idempotent
    assert POLICY.decide(attempt=1, error=sent, idempotent=True, rand=0.5) is not None
    assert POLICY.decide(attempt=1, error=server, idempotent=True, rand=0.5) is not None


# 11
@pytest.mark.parametrize(
    "error",
    [
        TelegramRetryAfter("sendMessage", 429, "Too Many Requests", retry_after=1),
        TelegramRetryAfter("sendMessage", 429, "Too Many Requests", retry_after=None),
        TelegramServerError("getMe", 500, "Internal Server Error"),
        TelegramNetworkError("getMe", sent=False, reason="ConnectError"),
        TelegramNetworkError("getMe", sent=True, reason="ReadTimeout"),
    ],
    ids=["429", "429-no-retry-after", "5xx", "net-not-sent", "net-sent"],
)
def test_attempt_limit_is_respected_for_every_error_kind(error: TelegramError) -> None:
    last_allowed = POLICY.max_attempts - 1

    assert POLICY.decide(attempt=last_allowed, error=error, idempotent=True, rand=0.5) is not None
    assert (
        POLICY.decide(attempt=POLICY.max_attempts, error=error, idempotent=True, rand=0.5) is None
    )


def test_retry_after_uses_server_value_plus_bounded_jitter() -> None:
    error = TelegramRetryAfter("sendMessage", 429, "Too Many Requests", retry_after=3)

    assert POLICY.decide(attempt=1, error=error, idempotent=False, rand=0.0) == 3.0
    delay = POLICY.decide(attempt=1, error=error, idempotent=False, rand=0.999)
    assert delay is not None
    assert 3.0 <= delay < 3.0 + POLICY.retry_after_jitter
