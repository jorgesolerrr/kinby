"""Read and write an instance's configuration files for clients, each read carrying its hash."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from pydantic import ValidationError

from kinby.contracts import (
    ConfigActor,
    ConfigFile,
    ConfigHistoryCommand,
    ConfigHistoryResult,
    FileHash,
    ManifestGetCommand,
    ManifestResult,
    ManifestSetCommand,
    ModelChoice,
    PriceSource,
    PromptGetCommand,
    PromptName,
    PromptResult,
    PromptSetCommand,
)
from kinby.core.errors import InvalidConfig, StaleWrite
from kinby.core.pricing import SHIPPED_PRICES
from kinby.instance import Instance, api_key_variable
from kinby.instance.config_changes import ConfigChangeLog, recorded_change
from kinby.instance.layout import MANIFEST_NAME, RECAP_NAME, SYSTEM_NAME
from kinby.instance.manifest import (
    edited_manifest,
    manifest_field_errors,
    offered_values,
    parse_manifest,
)
from kinby.instance.recap import DEFAULT_RECAP_LENS

_PROMPT_FILES = {
    PromptName.BEHAVIOR: ConfigFile(SYSTEM_NAME),
    PromptName.RECAP: ConfigFile(RECAP_NAME),
}
#: What each prompt reads as without its file. kinby ships no behavior prompt of its own.
_SHIPPED_PROMPTS = {PromptName.BEHAVIOR: "", PromptName.RECAP: DEFAULT_RECAP_LENS}


def _file_hash(content: bytes) -> FileHash:
    return FileHash(hashlib.sha256(content).hexdigest())


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write_over(path: Path, content: bytes, read: FileHash) -> None:
    """Replace the file the client read, staging beside it so no reader sees half a file."""
    if _file_hash(_read_bytes(path) or b"") != read:
        raise StaleWrite(f"{path.name} changed since it was read. Read it again.")
    staging = path.with_name(f".{path.name}.staging")
    staging.write_bytes(content)
    staging.replace(path)


def _manifest_result(content: bytes) -> ManifestResult:
    raw = parse_manifest(content.decode("utf-8"))
    return ManifestResult(
        values=offered_values(raw),
        model_choices=[
            ModelChoice(
                model=model,
                priced_from=PriceSource.MANIFEST if model in raw.prices else PriceSource.SHIPPED,
                key_set=bool(os.environ.get(api_key_variable(model))),
            )
            for model in sorted(SHIPPED_PRICES.keys() | raw.prices.keys())
        ],
        hash=_file_hash(content),
    )


class InstanceConfig:
    """The instance's configuration as the contract reads and writes it."""

    def __init__(self, instance: Instance) -> None:
        self._instance = instance

    async def get_prompt(self, command: PromptGetCommand) -> PromptResult:
        content = _read_bytes(self._instance.path / _PROMPT_FILES[command.name])
        if content is None:
            return PromptResult(
                content=_SHIPPED_PROMPTS[command.name], hash=_file_hash(b""), default=True
            )
        return PromptResult(
            content=content.decode("utf-8"), hash=_file_hash(content), default=False
        )

    async def set_prompt(self, command: PromptSetCommand) -> PromptResult:
        content = command.content.encode("utf-8")
        async with self._instance.config_lock:
            await asyncio.to_thread(self._write, _PROMPT_FILES[command.name], content, command.hash)
        return PromptResult(content=command.content, hash=_file_hash(content), default=False)

    async def get_manifest(self, command: ManifestGetCommand) -> ManifestResult:
        return _manifest_result((self._instance.path / MANIFEST_NAME).read_bytes())

    async def set_manifest(self, command: ManifestSetCommand) -> ManifestResult:
        path = self._instance.path / MANIFEST_NAME
        async with self._instance.config_lock:
            text = path.read_text(encoding="utf-8")
            try:
                edited = edited_manifest(text, command.values, command.prices)
            except ValidationError as exc:
                raise InvalidConfig(manifest_field_errors(exc)) from exc
            content = edited.encode("utf-8")
            await asyncio.to_thread(self._write, ConfigFile(MANIFEST_NAME), content, command.hash)
        return _manifest_result(content)

    async def history(self, command: ConfigHistoryCommand) -> ConfigHistoryResult:
        log = ConfigChangeLog(self._instance.manifest.state_dir)
        return ConfigHistoryResult(changes=log.history(command.file, command.limit))

    def _write(self, file: ConfigFile, content: bytes, read: FileHash) -> None:
        with recorded_change(self._instance, file, ConfigActor.APP):
            _write_over(self._instance.path / file, content, read)
