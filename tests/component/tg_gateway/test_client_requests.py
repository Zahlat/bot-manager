"""Group F: what each method actually sends to Telegram, and how results are parsed."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
import respx
from pydantic import SecretStr

from tg_gateway.client import BotClient
from tg_gateway.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
    User,
    WebhookInfo,
)

from .conftest import CHAT_ID, MESSAGE, ok

pytestmark = pytest.mark.component

MakeClient = Callable[..., BotClient]

KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=((InlineKeyboardButton(text="A", callback_data="x"),),)
)


def sent_json(route: respx.Route) -> dict[str, object]:
    request = route.calls.last.request
    assert request.headers["content-type"] == "application/json"
    payload: dict[str, object] = json.loads(request.content)
    return payload


# 27
async def test_send_message_defaults_html_and_no_link_preview(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=ok(MESSAGE))

    await make_client().send_message(CHAT_ID, "<b>hi</b>")

    assert sent_json(route) == {
        "chat_id": CHAT_ID,
        "text": "<b>hi</b>",
        "parse_mode": "HTML",
        "disable_notification": False,
        "link_preview_options": {"is_disabled": True},
    }


async def test_send_message_serializes_keyboard(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=ok(MESSAGE))

    await make_client().send_message(CHAT_ID, "hi", reply_markup=KEYBOARD)

    assert sent_json(route)["reply_markup"] == {
        "inline_keyboard": [[{"text": "A", "callback_data": "x"}]]
    }


# 28
async def test_none_fields_omitted_from_payload(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    route = api.post("/answerCallbackQuery").mock(return_value=ok(True))

    assert await make_client().answer_callback_query("cbq-1") is True

    assert sent_json(route) == {"callback_query_id": "cbq-1", "show_alert": False}


async def test_edit_message_text_payload(api: respx.MockRouter, make_client: MakeClient) -> None:
    route = api.post("/editMessageText").mock(return_value=ok(MESSAGE))

    await make_client().edit_message_text(CHAT_ID, 7, "new", reply_markup=KEYBOARD)

    payload = sent_json(route)
    assert payload["message_id"] == 7
    assert payload["text"] == "new"
    assert payload["parse_mode"] == "HTML"
    assert "reply_markup" in payload


# 29
async def test_send_document_by_file_id_posts_json(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    route = api.post("/sendDocument").mock(return_value=ok(MESSAGE))

    await make_client().send_document(CHAT_ID, "FILE_ID", caption="Lesson 1")

    assert sent_json(route) == {
        "chat_id": CHAT_ID,
        "document": "FILE_ID",
        "caption": "Lesson 1",
        "parse_mode": "HTML",
    }


# 30
async def test_send_document_upload_is_multipart_with_json_reply_markup(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    route = api.post("/sendDocument").mock(return_value=ok(MESSAGE))
    pdf = InputFile(filename="lesson.pdf", content=b"%PDF-1.4 fake")

    await make_client().send_document(CHAT_ID, pdf, reply_markup=KEYBOARD)

    request = route.calls.last.request
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'name="document"; filename="lesson.pdf"' in body
    assert b"Content-Type: application/pdf" in body
    assert b"%PDF-1.4 fake" in body
    assert b'name="chat_id"\r\n\r\n42\r\n' in body
    assert b'{"inline_keyboard":[[{"text":"A","callback_data":"x"}]]}' in body


# 31
async def test_get_updates_read_timeout_exceeds_long_poll_timeout(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    update = {"update_id": 5, "message": MESSAGE}
    route = api.post("/getUpdates").mock(return_value=ok([update]))

    updates = await make_client().get_updates(offset=5, timeout=30)

    assert updates == [Update.model_validate(update)]
    assert sent_json(route) == {"offset": 5, "timeout": 30, "limit": 100}
    assert route.calls.last.request.extensions["timeout"]["read"] == 40


# 32
async def test_injected_http_client_is_reused_and_not_closed(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    seen: list[httpx.Request] = []

    async def count(request: httpx.Request) -> None:
        seen.append(request)

    api.post("/getMe").mock(return_value=ok({"id": 1, "is_bot": True, "first_name": "Bot"}))
    async with httpx.AsyncClient(event_hooks={"request": [count]}) as http:
        client = make_client(http=http)
        await client.get_me()
        await client.get_me()

        assert len(seen) == 2
        assert not http.is_closed


async def test_get_me_parses_user(api: respx.MockRouter, make_client: MakeClient) -> None:
    api.post("/getMe").mock(
        return_value=ok({"id": 9, "is_bot": True, "first_name": "Bot", "username": "x_bot"})
    )

    me = await make_client().get_me()

    assert me == User(id=9, is_bot=True, first_name="Bot", username="x_bot")


async def test_set_webhook_sends_secret_value_and_allowed_updates(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    route = api.post("/setWebhook").mock(return_value=ok(True))

    done = await make_client().set_webhook(
        "https://bot.example/tg/abc",
        secret_token=SecretStr("s3cr3t_TOKEN-1"),
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

    assert done is True
    assert sent_json(route) == {
        "url": "https://bot.example/tg/abc",
        "secret_token": "s3cr3t_TOKEN-1",
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }


async def test_get_webhook_info_and_delete_webhook(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/getWebhookInfo").mock(
        return_value=ok(
            {
                "url": "https://bot.example/tg/abc",
                "has_custom_certificate": False,
                "pending_update_count": 3,
                "last_error_message": "Connection timed out",
            }
        )
    )
    delete = api.post("/deleteWebhook").mock(return_value=ok(True))
    client = make_client()

    info = await client.get_webhook_info()
    assert await client.delete_webhook(drop_pending_updates=True) is True

    assert isinstance(info, WebhookInfo)
    assert info.pending_update_count == 3
    assert info.last_error_message == "Connection timed out"
    assert sent_json(delete) == {"drop_pending_updates": True}


async def test_set_my_commands_payload(api: respx.MockRouter, make_client: MakeClient) -> None:
    route = api.post("/setMyCommands").mock(return_value=ok(True))

    await make_client().set_my_commands(
        [BotCommand(command="start", description="ابدأ")], language_code="ar"
    )

    assert sent_json(route) == {
        "commands": [{"command": "start", "description": "ابدأ"}],
        "language_code": "ar",
    }
