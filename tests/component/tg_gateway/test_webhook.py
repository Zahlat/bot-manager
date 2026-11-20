"""Slice 6: webhook endpoint. Verify, dedupe, hand off, ack fast; never leak details."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from tg_gateway.transport.webhook import InMemoryUpdateDeduper, create_webhook_router
from tg_gateway.types import Update

pytestmark = pytest.mark.component

PATH = "/tg/7Qx2Lm9Vb4Kc8Np1Rz6Ty3Wg5Hd0Fs"
SECRET = "s3cret_Webhook-token"
HEADER = "X-Telegram-Bot-Api-Secret-Token"
UPDATE = {
    "update_id": 77,
    "message": {"message_id": 1, "date": 0, "chat": {"id": 5, "type": "private"}, "text": "hi"},
}


class Recorder:
    def __init__(self, fail_times: int = 0) -> None:
        self.updates: list[Update] = []
        self.fail_times = fail_times

    async def __call__(self, update: Update) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("queue unavailable")
        self.updates.append(update)


class BrokenDeduper(InMemoryUpdateDeduper):
    async def claim(self, update_id: int) -> bool:
        raise ConnectionError("redis down")


def make_app(on_update: Recorder, **kwargs: object) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_webhook_router(
            path=PATH,
            secret_token=SecretStr(SECRET),
            on_update=on_update,
            **kwargs,  # type: ignore[arg-type]
        )
    )
    return app


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def client(recorder: Recorder) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(make_app(recorder, dedupe=InMemoryUpdateDeduper())):
        yield c


async def post(
    client: httpx.AsyncClient,
    body: object = UPDATE,
    *,
    secret: str | None = SECRET,
    content_type: str = "application/json",
) -> httpx.Response:
    headers = {"content-type": content_type}
    if secret is not None:
        headers[HEADER] = secret
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return await client.post(PATH, content=content, headers=headers)


async def test_valid_update_is_handed_off_and_acked(
    client: httpx.AsyncClient, recorder: Recorder
) -> None:
    response = await post(client)

    assert response.status_code == 200
    assert [u.update_id for u in recorder.updates] == [77]
    assert recorder.updates[0].message is not None


@pytest.mark.parametrize("secret", [None, "", "wrong", SECRET + "x", SECRET[:-1]])
async def test_webhook_rejects_missing_or_wrong_secret(
    client: httpx.AsyncClient, recorder: Recorder, secret: str | None
) -> None:
    response = await post(client, secret=secret)

    assert response.status_code == 401
    assert response.content == b""
    assert recorder.updates == []


async def test_secret_is_checked_before_the_body_is_read(client: httpx.AsyncClient) -> None:
    response = await post(client, b"x" * 2_000_000, secret="wrong")

    assert response.status_code == 401  # not 413: unauthenticated callers learn nothing


async def test_duplicate_update_id_is_acked_once(
    client: httpx.AsyncClient, recorder: Recorder
) -> None:
    first = await post(client)
    second = await post(client)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(recorder.updates) == 1


async def test_non_json_content_type_is_rejected(
    client: httpx.AsyncClient, recorder: Recorder
) -> None:
    response = await post(client, content_type="text/plain")

    assert response.status_code == 415
    assert recorder.updates == []


async def test_json_content_type_with_charset_is_accepted(client: httpx.AsyncClient) -> None:
    response = await post(client, content_type="application/json; charset=utf-8")

    assert response.status_code == 200


async def test_oversized_body_is_rejected(recorder: Recorder) -> None:
    async for c in client_for(make_app(recorder, max_body_bytes=100)):
        response = await post(c, {**UPDATE, "padding": "x" * 200})

        assert response.status_code == 413
        assert response.content == b""
    assert recorder.updates == []


@pytest.mark.parametrize(
    "body",
    [b"{not json", b"[]", json.dumps({"no_update_id": 1}).encode()],
    ids=["broken-json", "array", "missing-update-id"],
)
async def test_invalid_body_is_400_without_details(
    client: httpx.AsyncClient, recorder: Recorder, body: bytes
) -> None:
    response = await post(client, body)

    assert response.status_code == 400
    assert response.content == b""
    assert recorder.updates == []


async def test_hand_off_failure_returns_503_and_releases_the_claim() -> None:
    recorder = Recorder(fail_times=1)
    async for c in client_for(make_app(recorder, dedupe=InMemoryUpdateDeduper())):
        first = await post(c)
        retry = await post(c)  # Telegram retries the same update after a 5xx

        assert first.status_code == 503
        assert first.content == b""
        assert retry.status_code == 200
    assert [u.update_id for u in recorder.updates] == [77]


async def test_dedupe_store_failure_returns_503(recorder: Recorder) -> None:
    async for c in client_for(make_app(recorder, dedupe=BrokenDeduper())):
        response = await post(c)

        assert response.status_code == 503
    assert recorder.updates == []


async def test_works_without_dedupe(recorder: Recorder) -> None:
    async for c in client_for(make_app(recorder)):
        await post(c)
        await post(c)
    assert len(recorder.updates) == 2


def test_route_is_hidden_from_openapi(recorder: Recorder) -> None:
    assert PATH not in make_app(recorder).openapi()["paths"]


@pytest.mark.parametrize("path", ["tg/no-slash", "/", ""])
def test_bad_path_rejected(recorder: Recorder, path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        create_webhook_router(path=path, secret_token=SecretStr(SECRET), on_update=recorder)


@pytest.mark.parametrize("secret", ["", "has space", "x" * 257, "émoji"])
def test_secret_token_must_match_telegram_rules(recorder: Recorder, secret: str) -> None:
    with pytest.raises(ValueError, match="secret"):
        create_webhook_router(path=PATH, secret_token=SecretStr(secret), on_update=recorder)


# --------------------------------------------------------------------------- deduper


async def test_in_memory_deduper_claim_release_and_bound() -> None:
    dedupe = InMemoryUpdateDeduper(max_size=2)

    assert await dedupe.claim(1) is True
    assert await dedupe.claim(1) is False
    await dedupe.release(1)
    assert await dedupe.claim(1) is True

    await dedupe.claim(2)
    await dedupe.claim(3)  # evicts the oldest (1)
    assert await dedupe.claim(1) is True
