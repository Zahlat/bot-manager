"""Slice 2: signed callback_data codec.

callback_data comes back from the user's Telegram client, so it is attacker-controlled. The codec
appends a truncated HMAC so the bot can tell its own buttons from forged ones, and never exceeds
Telegram's 64-byte limit.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import SecretStr

from tg_gateway.callback import (
    CallbackCodec,
    CallbackDataError,
    CallbackDataTooLong,
    InvalidCallbackData,
)

pytestmark = pytest.mark.unit

KEY = SecretStr("k" * 32)
OTHER_KEY = SecretStr("o" * 32)
CODEC = CallbackCodec(KEY)

ALPHABET = string.ascii_letters + string.digits + "_.-"
part = st.text(alphabet=ALPHABET, min_size=1, max_size=12)
parts_list = st.lists(part, min_size=1, max_size=6)


def fits(parts: list[str]) -> bool:
    return len(":".join(parts)) <= CODEC.max_payload_bytes


# 1
@given(parts=parts_list)
def test_roundtrip_property(parts: list[str]) -> None:
    assume(fits(parts))

    assert CODEC.decode(CODEC.encode(*parts)) == tuple(parts)


def test_encoding_is_deterministic() -> None:
    assert CODEC.encode("12", "go", "345") == CODEC.encode("12", "go", "345")


# 2
@given(data=st.data())
def test_tampered_callback_data_rejected(data: st.DataObject) -> None:
    encoded = CODEC.encode("12", "send", "345")
    index = data.draw(st.integers(min_value=0, max_value=len(encoded) - 1), label="index")
    replacement = data.draw(st.characters(), label="replacement")
    assume(replacement != encoded[index])

    tampered = encoded[:index] + replacement + encoded[index + 1 :]

    with pytest.raises(InvalidCallbackData):
        CODEC.decode(tampered)


# 3
@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s[:-1],
        lambda s: s + "A",
        lambda s: s[1:],
        lambda s: "x" + s,
        lambda s: s.rsplit(":", 1)[0],  # tag stripped
        lambda s: s.rsplit(":", 1)[0] + ":",  # empty tag
    ],
    ids=["drop-last", "append", "drop-first", "prepend", "no-tag", "empty-tag"],
)
def test_truncated_or_extended_data_rejected(mutate: object) -> None:
    encoded = CODEC.encode("12", "send", "345")

    with pytest.raises(InvalidCallbackData):
        CODEC.decode(mutate(encoded))  # type: ignore[operator]  # parametrized lambdas


# 4
def test_tag_from_other_key_rejected() -> None:
    forged = CallbackCodec(OTHER_KEY).encode("12", "send", "345")

    with pytest.raises(InvalidCallbackData):
        CODEC.decode(forged)


# 5
@given(parts=parts_list)
def test_encoded_length_never_exceeds_64_bytes(parts: list[str]) -> None:
    try:
        encoded = CODEC.encode(*parts)
    except CallbackDataTooLong:
        assert not fits(parts)
    else:
        assert len(encoded.encode("utf-8")) <= 64


# 6
def test_too_long_payload_raises_on_encode() -> None:
    with pytest.raises(CallbackDataTooLong):
        CODEC.encode("a" * 30, "b" * 30)


# 7
@pytest.mark.parametrize("bad", ["a:b", "a b", "رياضيات", "", "a\x00", "a/b"])
def test_part_with_separator_or_disallowed_char_rejected_on_encode(bad: str) -> None:
    with pytest.raises(CallbackDataError) as exc_info:
        CODEC.encode("12", bad)

    assert not isinstance(exc_info.value, CallbackDataTooLong)


def test_encode_requires_at_least_one_part() -> None:
    with pytest.raises(CallbackDataError):
        CODEC.encode()


# 8
@given(garbage=st.text(max_size=80))
def test_garbage_input_only_ever_raises_invalid_callback_data(garbage: str) -> None:
    with pytest.raises(InvalidCallbackData):
        CODEC.decode(garbage)


# 9
def test_previous_key_accepted_for_decode_but_not_used_for_encode() -> None:
    old = CallbackCodec(OTHER_KEY)
    rotated = CallbackCodec(KEY, previous_keys=[OTHER_KEY])

    assert rotated.decode(old.encode("12", "go")) == ("12", "go")  # buttons already on screen
    assert rotated.encode("12", "go") == CODEC.encode("12", "go")  # new buttons use the new key
    with pytest.raises(InvalidCallbackData):
        old.decode(rotated.encode("12", "go"))


# 10
@pytest.mark.parametrize(
    "kwargs",
    [
        {"key": SecretStr("k" * 31)},
        {"key": KEY, "tag_bytes": 3},
        {"key": KEY, "tag_bytes": 33},
        {"key": KEY, "sep": ""},
        {"key": KEY, "sep": "::"},
        {"key": KEY, "sep": "a"},  # a separator that can appear inside parts
        {"key": KEY, "previous_keys": [SecretStr("short")]},
    ],
    ids=["short-key", "tag-3", "tag-33", "sep-empty", "sep-2", "sep-in-alphabet", "short-old"],
)
def test_bad_construction_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"."):
        CallbackCodec(**kwargs)  # type: ignore[arg-type]  # parametrized kwargs bag


# 11
def test_max_payload_bytes_matches_real_limit() -> None:
    assert CODEC.max_payload_bytes == 64 - 1 - 11  # sep + base64url(8 bytes) = 11 chars

    longest = CODEC.encode("a" * CODEC.max_payload_bytes)
    assert len(longest) == 64
    assert CODEC.decode(longest) == ("a" * CODEC.max_payload_bytes,)
    with pytest.raises(CallbackDataTooLong):
        CODEC.encode("a" * (CODEC.max_payload_bytes + 1))


def test_custom_tag_length_changes_budget() -> None:
    codec = CallbackCodec(KEY, tag_bytes=6)

    assert codec.max_payload_bytes == 64 - 1 - 8
    assert codec.decode(codec.encode("x")) == ("x",)
