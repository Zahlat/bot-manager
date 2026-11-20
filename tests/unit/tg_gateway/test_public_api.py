"""Slice 7: the package's public surface."""

from __future__ import annotations

import subprocess
import sys

import pytest

import tg_gateway

pytestmark = pytest.mark.unit


def test_every_public_name_is_importable() -> None:
    for name in tg_gateway.__all__:
        assert hasattr(tg_gateway, name), name


def test_importing_the_package_does_not_import_fastapi() -> None:
    code = (
        "import sys, tg_gateway; sys.exit('fastapi' in sys.modules or 'starlette' in sys.modules)"
    )

    result = subprocess.run([sys.executable, "-c", code], check=False, timeout=60)  # noqa: S603

    assert result.returncode == 0
