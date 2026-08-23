"""Errors raised by the instance package."""


class InstanceExistsError(Exception):
    """Raised when init would overwrite an existing instance."""


class ManifestError(ValueError):
    """Raised when an instance manifest cannot be loaded or validated."""
