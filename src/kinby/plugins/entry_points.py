"""Name the installed package a plugin entry point comes from."""

from importlib.metadata import EntryPoint


def distribution_label(entry_point: EntryPoint) -> str:
    """The entry point's distribution and version, the way a client names a package."""
    distribution = entry_point.dist
    if distribution is None:
        return entry_point.value
    return f"{distribution.name} {distribution.version}"
