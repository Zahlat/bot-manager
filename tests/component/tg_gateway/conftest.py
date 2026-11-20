"""Shared fixtures for BotClient component tests: mocked Bot API, fake clock, client factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from tg_gateway.client import BotClient

# Shaped like a real token on purpose, so the redaction tests catch leaks. Not a secret.
TOKEN = "123456:TEST-TOKEN-abcdefghijklmnopqrstuvwxyz0123"
BASE_URL = "https://tg.test"
CHAT_ID = 42

MESSAGE: dict[str, object] = {
    "message_id": 1,
    "date": 0,
    "chat": {"id": CHAT_ID, "type": "private"},
    "text": "hi",
}


def ok(result: object) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def too_many_requests(retry_after: int | None) -> httpx.Response:
    body: dict[str, object] = {
        "ok": False,
        "error_code": 429,
        "description": f"Too Many Requests: retry after {retry_after}",
    }
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return httpx.Response(429, json=body)


class FakeSleep:
    """Records requested delays instead of sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def fake_sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """Mocked Bot API; routes are paths like '/sendMessage'."""
    with respx.mock(base_url=f"{BASE_URL}/bot{TOKEN}", assert_all_called=False) as router:
        yield router


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def make_client(http: httpx.AsyncClient, fake_sleep: FakeSleep) -> Callable[..., BotClient]:
    """Build a BotClient with a fake clock and rand fixed at 0.0; any kwarg overrides a default."""

    def _make(**overrides: object) -> BotClient:
        kwargs: dict[str, object] = {
            "http": http,
            "base_url": BASE_URL,
            "sleep": fake_sleep,
            "rand": lambda: 0.0,
        }
        kwargs.update(overrides)
        return BotClient(SecretStr(TOKEN), **kwargs)  # type: ignore[arg-type]  # test-only kwargs bag

    return _make
