"""Write secrets to protected files, and keep the hub's own (ADR 0095)."""

import json
import os
from pathlib import Path

from kinby.contracts import HubSecretName
from kinby.core.errors import HubSecretNotFound


def write_protected(path: Path, body: str) -> None:
    """Close the file to everyone else before the first secret byte lands in it."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
    path.chmod(0o600)


def replace_protected(path: Path, body: str) -> None:
    """Replace the file through one rename.

    A failure leaves the previous file in place and removes the staging file,
    so the values are not left in a second copy.
    """
    staging = path.with_name(f"{path.name}.replacing")
    try:
        write_protected(staging, body)
        staging.replace(path)
    except OSError:
        staging.unlink(missing_ok=True)
        raise


class HubSecrets:
    """The hub secrets, by name, in one protected file beside the instance registry."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def values(self) -> dict[HubSecretName, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def save(self, name: HubSecretName, value: str) -> None:
        """Create or replace one hub secret."""
        self._write(self.values() | {name: value})

    def delete(self, name: HubSecretName) -> None:
        secrets = self.values()
        if secrets.pop(name, None) is None:
            raise HubSecretNotFound(f'Hub secret "{name}" was not found.')
        self._write(secrets)

    def _write(self, secrets: dict[HubSecretName, str]) -> None:
        replace_protected(self._path, json.dumps(secrets))
