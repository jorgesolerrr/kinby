import pytest

from kinby.plugins.hub import HUB_URL_VARIABLE


@pytest.fixture(autouse=True)
def _without_a_hub_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as an instance without a hub route, even inside a factory's container.

    The hub gives a factory's instances and the home instance a hub URL, and with it the model
    gets `hand_to_factory` and `hub_read`. A test that wants them sets the variable itself.
    """
    monkeypatch.delenv(HUB_URL_VARIABLE, raising=False)
