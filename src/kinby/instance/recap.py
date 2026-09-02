"""Load the instance's recap prompt."""

from dataclasses import dataclass
from pathlib import Path

from kinby.instance.layout import RECAP_NAME

DEFAULT_RECAP_LENS: str = (
    "Describe the turn's concrete outcome and decisions. "
    "Name one honest way the work could have gone differently."
)


@dataclass(frozen=True)
class RecapLens:
    """A recap lens, its instance path, and whether kinby supplied its text."""

    text: str
    path: Path
    uses_default: bool


def load_recap_lens(instance_path: Path) -> RecapLens:
    """Return the instance lens, or the shipped lens when its file is absent."""
    source = instance_path / RECAP_NAME
    try:
        text = source.read_text(encoding="utf-8").strip("\r\n")
    except FileNotFoundError:
        return RecapLens(DEFAULT_RECAP_LENS, source, uses_default=True)
    return RecapLens(text, source, uses_default=False)
