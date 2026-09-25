"""Find the files that exist only in a source checkout of kinby."""

from __future__ import annotations

from pathlib import Path


def checkout_path(relative: str) -> Path:
    """*relative* inside this source checkout.

    Schema generators write into the checkout. An installed wheel has none to write to.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "kinby").is_dir():
            return parent / relative
    raise RuntimeError(f"{relative} is written only from a source checkout of kinby")
