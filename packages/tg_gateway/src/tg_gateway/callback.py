"""Compact, HMAC-signed callback_data (Telegram limit: 1-64 bytes).

Format: ``part1:part2:...:TAG`` where
``TAG = base64url_nopad(HMAC-SHA256(key, DOMAIN + payload)[:tag_bytes])``.

callback_data is echoed back by the user's client, so it is untrusted: decode() rejects any byte
change. A valid tag proves *we* issued the button; it does not authorize the action. Use cases
still re-check permissions (docs/PLAN.md, Security §3).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import re
from collections.abc import Sequence

from pydantic import SecretStr

__all__ = [
    "MAX_CALLBACK_BYTES",
    "CallbackCodec",
    "CallbackDataError",
    "CallbackDataTooLong",
    "InvalidCallbackData",
]

MAX_CALLBACK_BYTES = 64
MIN_KEY_BYTES = 32
MIN_TAG_BYTES, MAX_TAG_BYTES = 4, 32

_DOMAIN = b"tgcb1\x00"  # versioned domain separation: this key signs nothing else
_PART = re.compile(r"[A-Za-z0-9_.\-]+")  # ASCII only: 1 char == 1 byte, separator can't appear


class CallbackDataError(ValueError):
    """Base class for callback-data problems."""


class CallbackDataTooLong(CallbackDataError):
    """encode(): the parts don't fit in 64 bytes with the tag. Never silently truncated."""


class InvalidCallbackData(CallbackDataError):
    """decode(): malformed, tampered with, or signed with an unknown key."""


def _key_bytes(key: SecretStr) -> bytes:
    raw = key.get_secret_value().encode("utf-8")
    if len(raw) < MIN_KEY_BYTES:
        raise ValueError(f"callback HMAC key must be at least {MIN_KEY_BYTES} bytes")
    return raw


class CallbackCodec:
    def __init__(
        self,
        key: SecretStr,
        *,
        previous_keys: Sequence[SecretStr] = (),
        tag_bytes: int = 8,
        sep: str = ":",
    ) -> None:
        if not MIN_TAG_BYTES <= tag_bytes <= MAX_TAG_BYTES:
            raise ValueError(f"tag_bytes must be in [{MIN_TAG_BYTES}, {MAX_TAG_BYTES}]")
        if len(sep) != 1 or not sep.isascii() or _PART.fullmatch(sep):
            raise ValueError("sep must be one ASCII character that cannot appear in a part")
        self._key = _key_bytes(key)
        self._decode_keys = (self._key, *(_key_bytes(k) for k in previous_keys))
        self._tag_bytes = tag_bytes
        self._tag_len = math.ceil(tag_bytes * 4 / 3)  # unpadded base64 length
        self._sep = sep

    def __repr__(self) -> str:
        return f"{type(self).__name__}(tag_bytes={self._tag_bytes}, sep={self._sep!r})"

    @property
    def max_payload_bytes(self) -> int:
        """Bytes available for the joined parts (separators between parts included)."""
        return MAX_CALLBACK_BYTES - len(self._sep) - self._tag_len

    def encode(self, *parts: str) -> str:
        if not parts:
            raise CallbackDataError("at least one part is required")
        for part in parts:
            if not _PART.fullmatch(part):
                raise CallbackDataError("parts must be non-empty and match [A-Za-z0-9_.-]+")
        payload = self._sep.join(parts)
        if len(payload) > self.max_payload_bytes:
            raise CallbackDataTooLong(
                f"payload is {len(payload)} bytes; the limit is {self.max_payload_bytes}"
            )
        return f"{payload}{self._sep}{self._tag(self._key, payload)}"

    def decode(self, data: str) -> tuple[str, ...]:
        if not data.isascii() or len(data) > MAX_CALLBACK_BYTES:
            raise InvalidCallbackData("malformed callback data")
        payload, sep, tag = data.rpartition(self._sep)
        if not sep or not payload or len(tag) != self._tag_len:
            raise InvalidCallbackData("malformed callback data")
        # Compare the *encoded* tags: decoding base64 would accept several spellings of one tag.
        tag_bytes = tag.encode("ascii")
        if not any(
            hmac.compare_digest(tag_bytes, self._tag(key, payload).encode("ascii"))
            for key in self._decode_keys
        ):
            raise InvalidCallbackData("bad signature")
        parts = tuple(payload.split(self._sep))
        if not all(_PART.fullmatch(part) for part in parts):  # only reachable with a valid tag
            raise InvalidCallbackData("malformed callback data")  # pragma: no cover
        return parts

    def _tag(self, key: bytes, payload: str) -> str:
        digest = hmac.new(key, _DOMAIN + payload.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest[: self._tag_bytes]).rstrip(b"=").decode("ascii")
