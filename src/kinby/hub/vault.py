"""The vault: each instance's sign-ins, each bound to one site, which the hub fills into that
site's pages on its browser route. Hub secrets are plaintext (ADR 0095), so the vault encrypts its
entries with a key of its own, and no value of them enters the instance (ADR 0113)."""

from __future__ import annotations

import base64
import binascii
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
from pydantic import TypeAdapter

from kinby.contracts import (
    VaultEntry,
    VaultEntryAddCommand,
    VaultEntryEditCommand,
    VaultEntryListResult,
    VaultEntryName,
    VaultField,
)
from kinby.core.errors import VaultEntryInvalid, VaultEntryNotFound, VaultFillRefused
from kinby.hub.secrets import replace_protected, write_protected

_TOTP_SECONDS = 30


@dataclass(frozen=True)
class _Kept:
    site: str
    username: str
    password: str
    totp_seed: str | None = None


_KEPT = TypeAdapter(dict[VaultEntryName, _Kept])


class Vault:
    """Every instance's vault, each instance's in one encrypted file of its own. *clock* gives
    the time a TOTP code is for."""

    def __init__(self, directory: Path, clock: Callable[[], float] = time.time) -> None:
        self._directory = directory
        self._clock = clock

    def list(self, instance_id: UUID) -> VaultEntryListResult:
        return VaultEntryListResult(
            entries=tuple(
                VaultEntry(name=name, site=kept.site)
                for name, kept in sorted(self._read(instance_id).items())
            )
        )

    def add(self, command: VaultEntryAddCommand) -> VaultEntryListResult:
        entries = self._read(command.instance_id)
        if command.name in entries:
            raise VaultEntryInvalid(f'The vault has an entry named "{command.name}".')
        seed = command.totp_seed
        entries[command.name] = _Kept(
            site=_site(command.site),
            username=command.username.get_secret_value(),
            password=command.password.get_secret_value(),
            totp_seed=None if seed is None else _checked_seed(seed.get_secret_value()),
        )
        self._write(command.instance_id, entries)
        return self.list(command.instance_id)

    def edit(self, command: VaultEntryEditCommand) -> VaultEntryListResult:
        entries = self._read(command.instance_id)
        kept = self._entry(entries, command.name)
        if command.site is not None:
            kept = replace(kept, site=_site(command.site))
        if command.username is not None:
            kept = replace(kept, username=command.username.get_secret_value())
        if command.password is not None:
            kept = replace(kept, password=command.password.get_secret_value())
        if command.totp_seed is not None:
            kept = replace(kept, totp_seed=_checked_seed(command.totp_seed.get_secret_value()))
        self._write(command.instance_id, entries | {command.name: kept})
        return self.list(command.instance_id)

    def delete(self, instance_id: UUID, name: VaultEntryName) -> VaultEntryListResult:
        entries = self._read(instance_id)
        self._entry(entries, name)
        del entries[name]
        self._write(instance_id, entries)
        return self.list(instance_id)

    def value(self, instance_id: UUID, name: str, field: VaultField, url: str) -> str:
        """The *field* of the entry *name*, to fill into the page at *url*, which must be on the
        entry's site."""
        kept = self._entry(self._read(instance_id), name)
        if (page := origin(url)) != kept.site:
            raise VaultFillRefused(
                f'The vault entry "{name}" fills pages on {kept.site} only, not on {page or url}.'
            )
        match field:
            case VaultField.USERNAME:
                return kept.username
            case VaultField.PASSWORD:
                return kept.password
            case VaultField.TOTP:
                if kept.totp_seed is None:
                    raise VaultFillRefused(f'The vault entry "{name}" has no TOTP seed.')
                return totp(kept.totp_seed, self._clock())

    def _entry(self, entries: dict[VaultEntryName, _Kept], name: str) -> _Kept:
        if name not in entries:
            raise VaultEntryNotFound(f'The vault has no entry named "{name}".')
        return entries[name]

    def _read(self, instance_id: UUID) -> dict[VaultEntryName, _Kept]:
        path = self._path(instance_id)
        if not path.exists():
            return {}
        return _KEPT.validate_json(self._key().decrypt(path.read_bytes()))

    def _write(self, instance_id: UUID, entries: dict[VaultEntryName, _Kept]) -> None:
        sealed = self._key().encrypt(_KEPT.dump_json(entries))
        replace_protected(self._path(instance_id), sealed.decode())

    def _key(self) -> Fernet:
        """The vault's key, made the first time an entry is written."""
        path = self._directory / "key"
        if not path.exists():
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            write_protected(path, Fernet.generate_key().decode())
        return Fernet(path.read_bytes())

    def _path(self, instance_id: UUID) -> Path:
        return self._directory / f"{instance_id}.vault"


def origin(url: str) -> str | None:
    """The origin of a web *url*, such as https://www.linkedin.com, or None for another URL."""
    parts = urlsplit(url.strip())
    try:
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.hostname}" + (f":{port}" if port else "")


def totp(seed: str, at: float) -> str:
    """The six-digit code of the base32 *seed* at the time *at*, as RFC 6238 counts it."""
    digest = hmac.digest(_seed_key(seed), int(at // _TOTP_SECONDS).to_bytes(8), "sha1")
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset : offset + 4]) & 0x7FFF_FFFF
    return f"{code % 1_000_000:06d}"


def _site(site: str) -> str:
    kept = origin(site)
    if kept is None:
        raise VaultEntryInvalid(f'"{site}" is not a web address, such as https://www.linkedin.com.')
    return kept


def _checked_seed(seed: str) -> str:
    _seed_key(seed)
    return seed


def _seed_key(seed: str) -> bytes:
    """The key of a base32 *seed*, in either case and with the spaces an authenticator shows it
    with."""
    key = seed.replace(" ", "").upper()
    try:
        return base64.b32decode(key + "=" * (-len(key) % 8))
    except binascii.Error:
        raise VaultEntryInvalid("The TOTP seed is not base32.") from None
