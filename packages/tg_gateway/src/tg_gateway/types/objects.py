"""The subset of Bot API objects this gateway uses. Field names follow the Bot API.

All models are frozen and ignore unknown fields: Telegram adds fields in every API release and
an unexpected field must never make an update unparseable.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TelegramObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class User(TelegramObject):
    id: int
    is_bot: bool
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class Chat(TelegramObject):
    id: int
    type: str  # "private" | "group" | "supergroup" | "channel"
    title: str | None = None
    username: str | None = None
    first_name: str | None = None


class Document(TelegramObject):
    file_id: str
    file_unique_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class InlineKeyboardButton(TelegramObject):
    text: str
    callback_data: str | None = None
    url: str | None = None


class InlineKeyboardMarkup(TelegramObject):
    inline_keyboard: tuple[tuple[InlineKeyboardButton, ...], ...]


class Message(TelegramObject):
    message_id: int
    date: int
    chat: Chat
    from_: User | None = Field(default=None, alias="from")
    text: str | None = None
    caption: str | None = None
    document: Document | None = None
    reply_markup: InlineKeyboardMarkup | None = None


class CallbackQuery(TelegramObject):
    id: str
    from_: User = Field(alias="from")
    chat_instance: str
    # Also used for "inaccessible" messages (date == 0); the shared fields still parse.
    message: Message | None = None
    data: str | None = None


class ChatMember(TelegramObject):
    user: User
    status: str  # "creator" | "administrator" | "member" | "restricted" | "left" | "kicked"


class ChatMemberUpdated(TelegramObject):
    chat: Chat
    from_: User = Field(alias="from")
    date: int
    old_chat_member: ChatMember
    new_chat_member: ChatMember


class Update(TelegramObject):
    update_id: int
    message: Message | None = None
    edited_message: Message | None = None
    callback_query: CallbackQuery | None = None
    my_chat_member: ChatMemberUpdated | None = None


class WebhookInfo(TelegramObject):
    url: str
    has_custom_certificate: bool
    pending_update_count: int
    ip_address: str | None = None
    last_error_date: int | None = None
    last_error_message: str | None = None
    max_connections: int | None = None
    allowed_updates: tuple[str, ...] | None = None


class BotCommand(TelegramObject):
    command: str
    description: str
