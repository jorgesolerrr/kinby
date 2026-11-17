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


@pytest.fixture(autouse=True)
def _without_a_coding_client_signed_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Run every test with no coding client signed in, even on a box whose clients are.

    With one signed in, the model gets `delegate_to_coding_client`. A test that wants it signs a
    client in itself.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path_factory.getbasetemp() / "codex"))
