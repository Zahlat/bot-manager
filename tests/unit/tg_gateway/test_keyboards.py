"""Slice 4: inline keyboard builder + pagination. Telegram's limits are enforced, never trusted."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tg_gateway.keyboards import (
    MAX_BUTTONS,
    MAX_ROW_WIDTH,
    InlineKeyboardBuilder,
    KeyboardError,
    paginate,
)
from tg_gateway.types import InlineKeyboardButton, InlineKeyboardMarkup

pytestmark = pytest.mark.unit


def texts(markup: InlineKeyboardMarkup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def cb(n: int) -> str:
    return f"cb{n}"


# --------------------------------------------------------------------------- builder


def test_buttons_fill_rows_and_row_starts_a_new_one() -> None:
    markup = (
        InlineKeyboardBuilder()
        .button("A", callback_data="a")
        .button("B", callback_data="b")
        .row()
        .button("C", url="https://example.com")
        .build()
    )

    assert texts(markup) == [["A", "B"], ["C"]]
    assert markup.inline_keyboard[1][0].url == "https://example.com"


def test_row_on_an_empty_row_does_not_create_empty_rows() -> None:
    markup = InlineKeyboardBuilder().row().button("A", callback_data="a").row().row().build()

    assert texts(markup) == [["A"]]


def test_empty_builder_builds_empty_keyboard() -> None:
    assert InlineKeyboardBuilder().build().inline_keyboard == ()


@pytest.mark.parametrize(
    ("count", "columns", "shape"), [(5, 2, [2, 2, 1]), (3, 3, [3]), (4, 1, [1] * 4)]
)
def test_adjust_reflows_all_buttons(count: int, columns: int, shape: list[int]) -> None:
    builder = InlineKeyboardBuilder()
    for n in range(count):
        builder.button(str(n), callback_data=cb(n)).row()

    markup = builder.adjust(columns).build()

    assert [len(row) for row in markup.inline_keyboard] == shape
    assert [b.text for row in markup.inline_keyboard for b in row] == [str(n) for n in range(count)]


@pytest.mark.parametrize("columns", [0, -1, MAX_ROW_WIDTH + 1])
def test_adjust_rejects_impossible_columns(columns: int) -> None:
    with pytest.raises(KeyboardError):
        InlineKeyboardBuilder().button("A", callback_data="a").adjust(columns)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": "", "callback_data": "a"},
        {"text": "   ", "callback_data": "a"},
        {"text": "A"},  # neither callback_data nor url
        {"text": "A", "callback_data": "a", "url": "https://x.org"},  # both
        {"text": "A", "callback_data": ""},
        {"text": "A", "callback_data": "x" * 65},
        {"text": "A", "callback_data": "ب" * 33},  # 33 chars but 66 UTF-8 bytes
        {"text": "A", "url": "http://example.com"},
        {"text": "A", "url": "javascript:alert(1)"},
        {"text": "A", "url": "https://"},
        {"text": "A", "url": "ftp://example.com/file.pdf"},
    ],
    ids=[
        "empty-text",
        "blank-text",
        "no-action",
        "two-actions",
        "empty-cb",
        "cb-65",
        "cb-66-bytes",
        "http",
        "javascript",
        "no-host",
        "ftp",
    ],
)
def test_invalid_buttons_rejected(kwargs: dict[str, str]) -> None:
    text = kwargs.pop("text")

    with pytest.raises(KeyboardError):
        InlineKeyboardBuilder().button(text, **kwargs).build()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"callback_data": "x" * 64},
        {"url": "https://example.com/a?b=c"},
        {"url": "tg://resolve?domain=telegram"},
    ],
    ids=["cb-64", "https", "tg"],
)
def test_valid_edge_buttons_accepted(kwargs: dict[str, str]) -> None:
    markup = InlineKeyboardBuilder().button("ok", **kwargs).build()

    assert texts(markup) == [["ok"]]


def test_row_wider_than_limit_rejected() -> None:
    builder = InlineKeyboardBuilder()
    for n in range(MAX_ROW_WIDTH + 1):
        builder.button(str(n), callback_data=cb(n))

    with pytest.raises(KeyboardError):
        builder.build()


def test_more_than_max_buttons_rejected() -> None:
    builder = InlineKeyboardBuilder()
    for n in range(MAX_BUTTONS + 1):
        builder.button(str(n), callback_data=cb(n))

    with pytest.raises(KeyboardError):
        builder.adjust(MAX_ROW_WIDTH).build()


def test_exactly_max_buttons_accepted() -> None:
    builder = InlineKeyboardBuilder()
    for n in range(MAX_BUTTONS):
        builder.button(str(n), callback_data=cb(n))

    markup = builder.adjust(MAX_ROW_WIDTH).build()

    assert sum(len(row) for row in markup.inline_keyboard) == MAX_BUTTONS


ops = st.lists(
    st.one_of(
        st.tuples(st.just("button"), st.integers(0, 70)),
        st.tuples(st.just("row"), st.just(0)),
        st.tuples(st.just("adjust"), st.integers(1, MAX_ROW_WIDTH)),
    ),
    max_size=150,
)


@given(ops=ops)
def test_any_successful_build_respects_every_limit(ops: list[tuple[str, int]]) -> None:
    builder = InlineKeyboardBuilder()
    try:
        for op, arg in ops:
            if op == "button":
                builder.button("b", callback_data="x" * max(1, arg))
            elif op == "row":
                builder.row()
            else:
                builder.adjust(arg)
        markup = builder.build()
    except KeyboardError:
        return

    rows = markup.inline_keyboard
    assert sum(len(row) for row in rows) <= MAX_BUTTONS
    assert all(0 < len(row) <= MAX_ROW_WIDTH for row in rows)
    for row in rows:
        for button in row:
            assert button.callback_data is not None
            assert 1 <= len(button.callback_data.encode()) <= 64


# --------------------------------------------------------------------------- paginate

ITEMS = [f"item{n}" for n in range(20)]


def item_button(name: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=name, callback_data=f"open:{name}")


def nav(page: int) -> str:
    return f"page:{page}"


def page_of(items: list[str], page: int, *, columns: int = 1) -> InlineKeyboardMarkup:
    builder = paginate(items, page=page, page_size=8, item=item_button, nav=nav, columns=columns)
    return builder.build()


def test_paginate_empty_list_has_no_buttons() -> None:
    assert page_of([], page=0).inline_keyboard == ()


def test_paginate_single_page_has_no_nav_row() -> None:
    assert texts(page_of(ITEMS[:3], page=0)) == [["item0"], ["item1"], ["item2"]]


def test_paginate_first_page_has_next_but_no_prev() -> None:
    markup = page_of(ITEMS, page=0)

    assert texts(markup)[:8] == [[f"item{n}"] for n in range(8)]
    nav_row = markup.inline_keyboard[-1]
    assert [b.text for b in nav_row] == ["1/3", "\u203a"]
    assert [b.callback_data for b in nav_row] == ["page:0", "page:1"]


def test_paginate_middle_page_has_both_directions() -> None:
    markup = page_of(ITEMS, page=1)

    assert texts(markup)[0] == ["item8"]
    nav_row = markup.inline_keyboard[-1]
    assert [b.text for b in nav_row] == ["\u2039", "2/3", "\u203a"]
    assert [b.callback_data for b in nav_row] == ["page:0", "page:1", "page:2"]


def test_paginate_last_page_is_partial_and_has_no_next() -> None:
    markup = page_of(ITEMS, page=2)

    assert texts(markup)[:-1] == [[f"item{n}"] for n in range(16, 20)]
    assert [b.text for b in markup.inline_keyboard[-1]] == ["\u2039", "3/3"]


@pytest.mark.parametrize(("requested", "shown"), [(99, "3/3"), (-5, "1/3")])
def test_paginate_clamps_out_of_range_pages(requested: int, shown: str) -> None:
    nav_row = page_of(ITEMS, page=requested).inline_keyboard[-1]

    assert shown in [b.text for b in nav_row]


def test_paginate_lays_items_out_in_columns() -> None:
    markup = page_of(ITEMS[:5], page=0, columns=2)

    assert texts(markup) == [["item0", "item1"], ["item2", "item3"], ["item4"]]


def test_paginate_returns_builder_callers_can_extend() -> None:
    builder = paginate(ITEMS[:2], page=0, page_size=8, item=item_button, nav=nav)

    markup = builder.row().button("Back", callback_data="back").build()

    assert texts(markup)[-1] == ["Back"]


@pytest.mark.parametrize("size", [0, -1, MAX_BUTTONS])
def test_paginate_rejects_bad_page_size(size: int) -> None:
    with pytest.raises(KeyboardError):
        paginate(ITEMS, page=0, page_size=size, item=item_button, nav=nav)


def test_paginate_custom_labels_for_rtl() -> None:
    builder = paginate(
        ITEMS, page=1, page_size=8, item=item_button, nav=nav, prev_label="→", next_label="←"
    )

    assert [b.text for b in builder.build().inline_keyboard[-1]] == ["→", "2/3", "←"]
