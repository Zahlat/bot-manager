"""Group G: our Pydantic models accept Telegram's real JSON shapes (fixtures in tests/fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tg_gateway.types import Update

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parent.parent / "fixtures" / "telegram"


def load(name: str) -> Update:
    return Update.model_validate_json((FIXTURES / name).read_bytes())


# 33
def test_text_message_update_parses() -> None:
    update = load("message_text.json")

    assert update.update_id == 870001001
    assert update.message is not None
    assert update.message.text == "/start res_123"
    assert update.message.chat.type == "private"
    assert update.message.from_ is not None
    assert update.message.from_.language_code == "ar"


def test_callback_query_update_parses() -> None:
    update = load("callback_query.json")

    query = update.callback_query
    assert query is not None
    assert query.data == "12:go:3:9f2a"
    assert query.from_.id == 5550001
    assert query.message is not None
    assert query.message.message_id == 1366


def test_my_chat_member_update_parses() -> None:
    update = load("my_chat_member.json")

    member = update.my_chat_member
    assert member is not None
    assert member.old_chat_member.status == "member"
    assert member.new_chat_member.status == "kicked"  # the user blocked the bot


def test_document_message_update_parses() -> None:
    update = load("message_document.json")

    assert update.message is not None
    document = update.message.document
    assert document is not None
    assert document.file_id == "BQACAgIAAxkBAAIBZmZ0ZXN0X2ZpbGVfaWQ"
    assert document.mime_type == "application/pdf"
    assert document.file_size == 2097152
    assert update.message.caption == "رياضيات - نموذج 2026"


# 34
def test_unknown_fields_are_ignored() -> None:
    raw = {
        "update_id": 1,
        "field_from_bot_api_v99": {"anything": True},
        "message": {
            "message_id": 2,
            "date": 0,
            "chat": {"id": 3, "type": "private", "brand_new_chat_field": 1},
            "text": "hi",
            "paid_star_count": 5,
        },
    }

    update = Update.model_validate(raw)

    assert update.message is not None
    assert update.message.text == "hi"


def test_models_are_immutable() -> None:
    update = load("message_text.json")

    with pytest.raises(ValueError, match="frozen"):
        update.update_id = 2
