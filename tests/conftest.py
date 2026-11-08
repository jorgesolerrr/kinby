import pytest

from kinby.contracts import SearchBackend
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE


@pytest.fixture(autouse=True)
def _without_a_hub_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as an instance without a hub route, even inside a factory's container.

    The hub gives a factory's instances and the home instance a hub URL, and with it the model
    gets `hand_to_factory` and `hub_write`. A test that wants them sets the variable itself.
    """
    monkeypatch.delenv(HUB_URL_VARIABLE, raising=False)
    monkeypatch.delenv(HUB_SCOPES_VARIABLE, raising=False)


@pytest.fixture(autouse=True)
def _without_a_search_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test without a search backend's key, even on a box that holds one.

    With the key, the model gets `web_search`. A test that wants it sets the variable itself.
    """
    for backend in SearchBackend:
        monkeypatch.delenv(backend.key_variable, raising=False)
