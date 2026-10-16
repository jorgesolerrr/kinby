import pytest

from kinby.plugins.intake import HUB_URL_VARIABLE


@pytest.fixture(autouse=True)
def _outside_a_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as an instance outside any factory, even inside a factory's container.

    The hub gives each factory instance a hub URL, and with it the model gets the
    `hand_to_factory` tool. A test that wants it sets the variable itself.
    """
    monkeypatch.delenv(HUB_URL_VARIABLE, raising=False)
