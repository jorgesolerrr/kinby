"""Browser profiles: each instance's named signed-in states. The hub keeps them and opens its
browsers with them, so not one cookie of a profile enters the instance (ADR 0113)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from kinby.contracts import BrowserProfile, BrowserProfileListResult, BrowserProfileName
from kinby.core.errors import BrowserProfileNameTaken, BrowserProfileNotFound
from kinby.hub.secrets import replace_protected

#: Playwright's storage state: a context's cookies, and each origin's local storage.
type StorageState = dict[str, JsonValue]


@dataclass(frozen=True)
class _Kept:
    site: str
    state: StorageState


_KEPT = TypeAdapter(dict[BrowserProfileName, _Kept])


class BrowserProfiles:
    """Every instance's browser profiles, each instance's in one protected file of its own."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def list(self, instance_id: UUID) -> BrowserProfileListResult:
        return BrowserProfileListResult(
            profiles=tuple(
                BrowserProfile(name=name, site=kept.site)
                for name, kept in sorted(self._read(instance_id).items())
            )
        )

    def state(self, instance_id: UUID, name: BrowserProfileName) -> StorageState:
        return self._profile(self._read(instance_id), name).state

    def save(
        self, instance_id: UUID, name: BrowserProfileName, site: str, state: StorageState
    ) -> BrowserProfile:
        """Create the profile, or replace the one by that name."""
        self._write(instance_id, self._read(instance_id) | {name: _Kept(site, state)})
        return BrowserProfile(name=name, site=site)

    def refresh(self, instance_id: UUID, name: BrowserProfileName, state: StorageState) -> None:
        """Keep the profile's newer *state*, unless the user deleted or renamed it since."""
        profiles = self._read(instance_id)
        if (kept := profiles.get(name)) is not None:
            self._write(instance_id, profiles | {name: _Kept(kept.site, state)})

    def rename(
        self, instance_id: UUID, name: BrowserProfileName, new_name: BrowserProfileName
    ) -> BrowserProfileListResult:
        profiles = self._read(instance_id)
        kept = self._profile(profiles, name)
        if new_name in profiles:
            raise BrowserProfileNameTaken(f'The instance has a browser profile named "{new_name}".')
        del profiles[name]
        self._write(instance_id, profiles | {new_name: kept})
        return self.list(instance_id)

    def delete(self, instance_id: UUID, name: BrowserProfileName) -> BrowserProfileListResult:
        profiles = self._read(instance_id)
        self._profile(profiles, name)
        del profiles[name]
        self._write(instance_id, profiles)
        return self.list(instance_id)

    def _profile(self, profiles: dict[BrowserProfileName, _Kept], name: str) -> _Kept:
        if name not in profiles:
            raise BrowserProfileNotFound(f'The instance has no browser profile named "{name}".')
        return profiles[name]

    def _read(self, instance_id: UUID) -> dict[BrowserProfileName, _Kept]:
        path = self._path(instance_id)
        return _KEPT.validate_json(path.read_bytes()) if path.exists() else {}

    def _write(self, instance_id: UUID, profiles: dict[BrowserProfileName, _Kept]) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        replace_protected(self._path(instance_id), _KEPT.dump_json(profiles).decode())

    def _path(self, instance_id: UUID) -> Path:
        return self._directory / f"{instance_id}.json"
