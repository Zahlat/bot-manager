"""Inline keyboard builder that enforces Telegram's limits, plus a list paginator.

Limits: each button has exactly one action (callback_data of 1-64 bytes, or a URL); a row holds
at most 8 buttons; a keyboard holds at most 100. URLs must be https:// or tg:// here; the
allowed-domain list is a policy decision and belongs to tg_flow's validator.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Self
from urllib.parse import urlsplit

from tg_gateway.callback import MAX_CALLBACK_BYTES
from tg_gateway.types import InlineKeyboardButton, InlineKeyboardMarkup

__all__ = ["MAX_BUTTONS", "MAX_ROW_WIDTH", "InlineKeyboardBuilder", "KeyboardError", "paginate"]

MAX_BUTTONS = 100
MAX_ROW_WIDTH = 8
_NAV_BUTTONS = 3  # prev, "n/m", next
_URL_SCHEMES = frozenset({"https", "tg"})


class KeyboardError(ValueError):
    """The keyboard would break a Telegram limit or contains an unsafe button."""


def _check_button(button: InlineKeyboardButton) -> None:
    if not button.text.strip():
        raise KeyboardError("button text must not be empty")
    if (button.callback_data is None) == (button.url is None):
        raise KeyboardError("a button needs exactly one of callback_data or url")
    if button.callback_data is not None:
        size = len(button.callback_data.encode("utf-8"))
        if not 1 <= size <= MAX_CALLBACK_BYTES:
            raise KeyboardError(f"callback_data must be 1-{MAX_CALLBACK_BYTES} bytes, got {size}")
    if button.url is not None:
        parts = urlsplit(button.url)
        if parts.scheme not in _URL_SCHEMES or (parts.scheme == "https" and not parts.netloc):
            raise KeyboardError("button URLs must be https:// (with a host) or tg://")


class InlineKeyboardBuilder:
    def __init__(self) -> None:
        self._rows: list[list[InlineKeyboardButton]] = [[]]

    def button(
        self, text: str, *, callback_data: str | None = None, url: str | None = None
    ) -> Self:
        return self.add(InlineKeyboardButton(text=text, callback_data=callback_data, url=url))

    def add(self, *buttons: InlineKeyboardButton) -> Self:
        """Append ready-made buttons to the current row (each is validated)."""
        for button in buttons:
            _check_button(button)
            self._rows[-1].append(button)
        return self

    def row(self) -> Self:
        """Start a new row (no-op if the current row is still empty)."""
        if self._rows[-1]:
            self._rows.append([])
        return self

    def adjust(self, columns: int) -> Self:
        """Re-flow every button added so far into rows of `columns`."""
        if not 1 <= columns <= MAX_ROW_WIDTH:
            raise KeyboardError(f"columns must be 1-{MAX_ROW_WIDTH}")
        flat = [button for row in self._rows for button in row]
        self._rows = [flat[i : i + columns] for i in range(0, len(flat), columns)] or [[]]
        return self

    def build(self) -> InlineKeyboardMarkup:
        rows = [row for row in self._rows if row]
        if any(len(row) > MAX_ROW_WIDTH for row in rows):
            raise KeyboardError(f"a row may hold at most {MAX_ROW_WIDTH} buttons")
        if sum(len(row) for row in rows) > MAX_BUTTONS:
            raise KeyboardError(f"a keyboard may hold at most {MAX_BUTTONS} buttons")
        return InlineKeyboardMarkup(inline_keyboard=tuple(tuple(row) for row in rows))


def paginate[T](
    items: Sequence[T],
    *,
    page: int,
    page_size: int,
    item: Callable[[T], InlineKeyboardButton],
    nav: Callable[[int], str],
    columns: int = 1,
    prev_label: str = "\u2039",
    next_label: str = "\u203a",
) -> InlineKeyboardBuilder:
    """One page of `items` plus a "prev n/m next" row. `page` is 0-based and clamped into range.

    `nav(page)` returns the callback_data that opens that page. Returns the builder so callers can
    append their own rows (Back, Home) before build().
    """
    if not 1 <= page_size <= MAX_BUTTONS - _NAV_BUTTONS:
        raise KeyboardError(f"page_size must be 1-{MAX_BUTTONS - _NAV_BUTTONS}")
    pages = max(1, math.ceil(len(items) / page_size))
    page = min(max(page, 0), pages - 1)

    builder = InlineKeyboardBuilder()
    for n, entry in enumerate(items[page * page_size : (page + 1) * page_size]):
        if n and n % columns == 0:
            builder.row()
        builder.add(item(entry))

    if pages > 1:
        builder.row()
        if page > 0:
            builder.button(prev_label, callback_data=nav(page - 1))
        builder.button(f"{page + 1}/{pages}", callback_data=nav(page))
        if page < pages - 1:
            builder.button(next_label, callback_data=nav(page + 1))
    return builder
