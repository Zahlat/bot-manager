"""Group E: the bot token never leaks through exceptions, tracebacks, logs or repr.

The token is part of every Bot API URL, and httpx exceptions/logs carry the URL, so these
tests deliberately inject failures whose *messages contain the token* and check nothing of it
survives into what our code raises or logs.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable

import httpx
import pytest
import respx

from tg_gateway.client import BotClient
from tg_gateway.errors import TelegramError

from .conftest import BASE_URL, CHAT_ID, MESSAGE, TOKEN, ok, too_many_requests

pytestmark = pytest.mark.component

MakeClient = Callable[..., BotClient]

LEAKY_URL = f"{BASE_URL}/bot{TOKEN}/sendMessage"
SECRET_PART = TOKEN.split(":", 1)[1]  # the part after the bot id


def _json(code: int) -> httpx.Response:
    return httpx.Response(code, json={"ok": False, "error_code": code, "description": "x"})


FAILURES: dict[str, list[httpx.Response | Exception]] = {
    "403": [_json(403)],
    "500": [_json(500)],
    "connect-error-exhausted": [httpx.ConnectError(f"cannot connect to {LEAKY_URL}")] * 4,
    "read-timeout": [httpx.ReadTimeout(f"timed out reading {LEAKY_URL}")],
    "non-json-200": [httpx.Response(200, text="<html>")],
    "429-above-cap": [too_many_requests(120)],
}


# 24
@pytest.mark.parametrize("side_effect", list(FAILURES.values()), ids=list(FAILURES))
async def test_token_absent_from_every_raised_error(
    api: respx.MockRouter, make_client: MakeClient, side_effect: list[httpx.Response | Exception]
) -> None:
    api.post("/sendMessage").mock(side_effect=side_effect)

    with pytest.raises(TelegramError) as exc_info:
        await make_client().send_message(CHAT_ID, "hi")

    exc = exc_info.value
    rendered = "".join(traceback.format_exception(exc))  # includes __cause__ / __context__
    for text in (str(exc), repr(exc), rendered):
        assert SECRET_PART not in text
    assert exc.__cause__ is None
    assert exc.__context__ is None


# 25
async def test_token_absent_from_logs(
    api: respx.MockRouter, make_client: MakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)  # worst case: someone turns on DEBUG globally in prod
    api.post("/sendMessage").mock(
        side_effect=[
            too_many_requests(1),
            httpx.ConnectError(f"cannot connect to {LEAKY_URL}"),
            ok(MESSAGE),
        ]
    )

    await make_client().send_message(CHAT_ID, "hi")

    assert "sendMessage" in caplog.text  # our retry log lines were captured...
    assert SECRET_PART not in caplog.text  # ...and none of them (nor httpx's) carry the token


# 26
def test_bot_client_repr_hides_token(make_client: MakeClient) -> None:
    client = make_client()

    assert SECRET_PART not in repr(client)
    assert SECRET_PART not in str(client)
