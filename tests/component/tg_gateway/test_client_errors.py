"""Group C: Bot API error responses map to the right exception type, and 4xx are never retried."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from tg_gateway.client import BotClient
from tg_gateway.errors import (
    InvalidFileId,
    MessageNotModified,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramConflict,
    TelegramDecodeError,
    TelegramForbidden,
    TelegramServerError,
    TelegramUnauthorized,
)

from .conftest import CHAT_ID, FakeSleep, ok

pytestmark = pytest.mark.component

MakeClient = Callable[..., BotClient]


def api_error(code: int, description: str) -> httpx.Response:
    return httpx.Response(code, json={"ok": False, "error_code": code, "description": description})


# 12
async def test_403_raises_forbidden_without_retry(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/sendMessage").mock(
        return_value=api_error(403, "Forbidden: bot was blocked by the user")
    )

    with pytest.raises(TelegramForbidden) as exc_info:
        await make_client().send_message(CHAT_ID, "hi")

    assert exc_info.value.error_code == 403
    assert exc_info.value.method == "sendMessage"
    assert route.call_count == 1
    assert fake_sleep.calls == []


# 13
async def test_400_message_not_modified_maps_to_subclass(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/editMessageText").mock(
        return_value=api_error(
            400,
            "Bad Request: message is not modified: specified new message content and reply "
            "markup are exactly the same as a current content and reply markup of the message",
        )
    )

    with pytest.raises(MessageNotModified) as exc_info:
        await make_client().edit_message_text(CHAT_ID, 1, "same")

    assert isinstance(exc_info.value, TelegramBadRequest)


# 14
async def test_400_wrong_file_identifier_maps_to_invalid_file_id(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/sendDocument").mock(
        return_value=api_error(400, "Bad Request: wrong file identifier/HTTP URL specified")
    )

    with pytest.raises(InvalidFileId) as exc_info:
        await make_client().send_document(CHAT_ID, "STALE_FILE_ID")

    assert isinstance(exc_info.value, TelegramBadRequest)


async def test_400_other_maps_to_plain_bad_request(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/sendMessage").mock(return_value=api_error(400, "Bad Request: chat not found"))

    with pytest.raises(TelegramBadRequest) as exc_info:
        await make_client().send_message(CHAT_ID, "hi")

    assert type(exc_info.value) is TelegramBadRequest
    assert exc_info.value.description == "Bad Request: chat not found"


# 15
async def test_401_unauthorized_and_409_conflict_map_correctly(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/getMe").mock(return_value=api_error(401, "Unauthorized"))
    api.post("/getUpdates").mock(
        return_value=api_error(
            409,
            "Conflict: terminated by other getUpdates request; make sure that only one "
            "bot instance is running",
        )
    )
    client = make_client()

    with pytest.raises(TelegramUnauthorized):
        await client.get_me()
    with pytest.raises(TelegramConflict):
        await client.get_updates()


# 16
async def test_unknown_error_code_maps_to_base_api_error(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/sendMessage").mock(return_value=api_error(418, "I'm a teapot"))

    with pytest.raises(TelegramAPIError) as exc_info:
        await make_client().send_message(CHAT_ID, "hi")

    assert type(exc_info.value) is TelegramAPIError
    assert exc_info.value.error_code == 418


# 17
async def test_200_non_json_raises_decode_error(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/getMe").mock(return_value=httpx.Response(200, text="<html>not json</html>"))

    with pytest.raises(TelegramDecodeError):
        await make_client().get_me()


async def test_200_result_with_wrong_shape_raises_decode_error(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/getMe").mock(return_value=ok("not a user object"))

    with pytest.raises(TelegramDecodeError):
        await make_client().get_me()


# 18
async def test_502_html_from_proxy_raises_server_error(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/sendMessage").mock(
        return_value=httpx.Response(502, text="<html><h1>502 Bad Gateway</h1></html>")
    )

    with pytest.raises(TelegramServerError) as exc_info:
        await make_client().send_message(CHAT_ID, "hi")

    assert exc_info.value.error_code == 502
