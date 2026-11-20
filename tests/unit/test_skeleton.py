import importlib

import pytest

PACKAGES = ["platform_kit", "tg_gateway", "tg_flow", "curriculum_bot"]


@pytest.mark.unit
@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports_and_has_version(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__version__ == "0.1.0"
