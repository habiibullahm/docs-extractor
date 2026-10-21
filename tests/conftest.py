import pytest

from docex.providers.llm import Cooldown


@pytest.fixture(autouse=True)
def _no_leftover_cooldown():
    Cooldown.clear()
    yield
    Cooldown.clear()
