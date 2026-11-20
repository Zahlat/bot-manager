"""Slice 7: the echo bot end to end (client, polling, dispatcher, handlers) on a mocked API."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx
from pydantic import SecretStr

from tg_gateway import BotClient, CallbackCodec

from .conftest import CHAT_ID, MESSAGE, ok

pytestmark = pytest.mark.component

MakeClient = Callable[..., BotClient]
EXAMPLE = Path(__file__).parents[3] / "packages" / "tg_gateway" / "examples" / "echo_bot.py"
CODEC = CallbackCodec(SecretStr("test-callback-key-of-32-bytes-ok!!"))
USER = {"id": CHAT_ID, "is_bot": False, "first_name": "Sami"}
CHAT = {"id": CHAT_ID, "type": "private"}


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("echo_bot", EXAMPLE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["echo_bot"] = module
    spec.loader.exec_module(module)
    return module


def message_update(update_id: int, body: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "date": 0, "chat": CHAT, "from": USER, "text": body},
    }


def callback_update(update_id: int, data: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cbq-1",
            "from": USER,
            "chat_instance": "ci",
            "message": {"message_id": 1, "date": 0, "chat": CHAT},
            "data": data,
        },
    }


async def drive(
    api: respx.MockRouter, client: BotClient, updates: list[dict[str, object]], expect_calls: int
) -> list[httpx.Request]:
    """Feed `updates` through one getUpdates batch; stop once `expect_calls` replies were sent."""
    stop = asyncio.Event()
    sent: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if len(sent) >= expect_calls:
            stop.set()
        return ok(True) if request.url.path.endswith("answerCallbackQuery") else ok(MESSAGE)

    api.post("/getMe").mock(return_value=ok({**USER, "is_bot": True, "username": "echo_test_bot"}))
    api.post("/deleteWebhook").mock(return_value=ok(True))
    polls = api.post("/getUpdates").mock(side_effect=[ok(updates)] + [ok([])] * 50)
    api.post("/sendMessage").mock(side_effect=reply)
    api.post("/answerCallbackQuery").mock(side_effect=reply)

    await asyncio.wait_for(load_example().run_bot(client, CODEC, stop=stop), timeout=3)

    final = json.loads(polls.calls.last.request.content)
    assert final["offset"] == max(int(str(u["update_id"])) for u in updates) + 1  # confirmed
    return sent


async def test_echo_bot_escapes_and_echoes_text(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    sent = await drive(api, make_client(), [message_update(10, "<b>hi</b> & bye")], expect_calls=1)

    payload = json.loads(sent[0].content)
    assert payload["chat_id"] == CHAT_ID
    assert payload["text"] == "&lt;b&gt;hi&lt;/b&gt; &amp; bye"


async def test_echo_bot_start_shows_a_signed_ping_button(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    sent = await drive(api, make_client(), [message_update(11, "/start")], expect_calls=1)

    payload = json.loads(sent[0].content)
    button = payload["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == "Ping"
    assert CODEC.decode(button["callback_data"]) == ("ping",)


async def test_echo_bot_answers_signed_ping_and_rejects_forged_one(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    updates = [callback_update(12, CODEC.encode("ping")), callback_update(13, "ping:forged0000")]

    sent = await drive(api, make_client(), updates, expect_calls=2)

    answers = [json.loads(r.content)["text"] for r in sent]
    assert answers == ["pong", "Invalid button"]


async def test_echo_bot_main_exits_with_2_without_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here

    assert await load_example().main() == 2
