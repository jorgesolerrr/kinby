import pytest

from kinby.plugins.intake import INTAKE_URL_VARIABLE


@pytest.fixture(autouse=True)
def _outside_a_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as an instance outside any factory, even inside a factory's container.

    The hub gives each factory instance an intake URL, and with it the model gets the
    `hand_to_factory` tool. A test that wants it sets the variable itself.
    """
    monkeypatch.delenv(INTAKE_URL_VARIABLE, raising=False)
