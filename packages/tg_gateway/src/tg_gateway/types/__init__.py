"""Typed Bot API objects (responses and updates) plus request-side inputs."""

from dataclasses import dataclass
from typing import Literal

from tg_gateway.types.objects import (
    BotCommand,
    CallbackQuery,
    Chat,
    ChatMember,
    ChatMemberUpdated,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    Update,
    User,
    WebhookInfo,
)

ParseMode = Literal["HTML", "MarkdownV2"]


@dataclass(frozen=True, slots=True)
class InputFile:
    """A file to upload as multipart. Held in memory so a retry can resend the same bytes."""

    filename: str
    content: bytes
    mime: str = "application/pdf"

    def __repr__(self) -> str:  # never dump megabytes of PDF into a log line
        return (
            f"InputFile(filename={self.filename!r}, size={len(self.content)}, mime={self.mime!r})"
        )


__all__ = [
    "BotCommand",
    "CallbackQuery",
    "Chat",
    "ChatMember",
    "ChatMemberUpdated",
    "Document",
    "InlineKeyboardButton",
    "InlineKeyboardMarkup",
    "InputFile",
    "Message",
    "ParseMode",
    "TelegramObject",
    "Update",
    "User",
    "WebhookInfo",
]
