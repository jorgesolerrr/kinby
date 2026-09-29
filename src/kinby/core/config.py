"""Read and write an instance's configuration files for clients, each read carrying its hash."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from kinby.contracts import (
    BashPermissions,
    ConfigActor,
    ConfigFile,
    ConfigHistoryCommand,
    ConfigHistoryResult,
    DenyPattern,
    FileHash,
    PackageConfigGetCommand,
    PackageConfigResult,
    PackageConfigSetCommand,
    PermissionsGetCommand,
    PermissionsResult,
    PermissionsSetCommand,
    PromptGetCommand,
    PromptName,
    PromptResult,
    PromptSetCommand,
    RecreateReason,
)
from kinby.core.errors import InvalidConfig, PackageConfigNotFound, StaleWrite
from kinby.instance import Instance
from kinby.instance.config_changes import ConfigChangeLog, recorded_change
from kinby.instance.layout import PERMISSIONS_NAME, RECAP_NAME, SYSTEM_NAME
from kinby.instance.permissions import (
    SHIPPED_BASH_DENY,
    SHIPPED_POLICY,
    BashPolicy,
    GatePolicy,
    bash_regex_errors,
    exceeds_ceiling,
    parse_permissions,
    permissions_toml,
)
from kinby.instance.recap import DEFAULT_RECAP_LENS
from kinby.packages import (
    PACKAGE_CONFIG_NAME,
    PackageConfig,
    SetupField,
    config_field_errors,
    load_package,
    package_config_yaml,
    validate_package_config,
)

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


def package_config_hash(instance: Instance) -> FileHash | None:
    """The hash of a packaged instance's package.yaml. None for a vanilla instance."""
    if instance.manifest.package is None:
        return None
    return _file_hash(_read_bytes(instance.path / PACKAGE_CONFIG_NAME) or b"")


class InstanceConfig:
    """The instance's configuration as the contract reads and writes it.

    *booted_package_config* is the hash of the package.yaml the instance validated at boot.
    """

    def __init__(self, instance: Instance, booted_package_config: FileHash | None = None) -> None:
        self._instance = instance
        self._booted_package_config = booted_package_config

    def restart_reasons(self) -> list[RecreateReason]:
        """What changed on disk since boot that applies only once the instance is recreated."""
        booted = self._booted_package_config
        if booted is None or package_config_hash(self._instance) == booted:
            return []
        return [RecreateReason.PACKAGE_CONFIG]

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

    async def get_permissions(self, command: PermissionsGetCommand) -> PermissionsResult:
        content = _read_bytes(self._instance.path / PERMISSIONS_NAME)
        if content is None:
            return _permissions_result(SHIPPED_POLICY, _file_hash(b""))
        return _permissions_result(parse_permissions(content), _file_hash(content))

    async def set_permissions(self, command: PermissionsSetCommand) -> PermissionsResult:
        policy = GatePolicy(
            mode=command.mode,
            ceiling=command.ceiling,
            tools=command.tools,
            bash=BashPolicy(deny=tuple(command.bash.deny), ask=tuple(command.bash.ask)),
        )
        errors = bash_regex_errors(policy.bash)
        if exceeds_ceiling(policy.mode, policy.ceiling):
            errors["mode"] = f"{policy.mode} is above the ceiling, {policy.ceiling}."
        if errors:
            raise InvalidConfig(errors)
        content = permissions_toml(policy).encode("utf-8")
        async with self._instance.config_lock:
            await asyncio.to_thread(
                self._write, ConfigFile(PERMISSIONS_NAME), content, command.hash
            )
        return _permissions_result(policy, _file_hash(content))

    async def get_package_config(self, command: PackageConfigGetCommand) -> PackageConfigResult:
        config, _ = self._package_config()
        content = _read_bytes(self._instance.path / PACKAGE_CONFIG_NAME) or b""
        values = yaml.safe_load(content) or {}
        return PackageConfigResult(
            schema=config.model_json_schema(),
            values=values if isinstance(values, dict) else {},
            hash=_file_hash(content),
        )

    async def set_package_config(self, command: PackageConfigSetCommand) -> PackageConfigResult:
        config, fields = self._package_config()
        try:
            validated = validate_package_config(config, fields, command.values)
        except ValidationError as exc:
            raise InvalidConfig(config_field_errors(exc)) from exc
        content = package_config_yaml(validated).encode("utf-8")
        async with self._instance.config_lock:
            await asyncio.to_thread(
                self._write, ConfigFile(PACKAGE_CONFIG_NAME), content, command.hash
            )
        return PackageConfigResult(
            schema=config.model_json_schema(),
            values=validated.model_dump(mode="json"),
            hash=_file_hash(content),
        )

    async def history(self, command: ConfigHistoryCommand) -> ConfigHistoryResult:
        log = ConfigChangeLog(self._instance.manifest.state_dir)
        return ConfigHistoryResult(changes=log.history(command.file, command.limit))

    def _package_config(self) -> tuple[type[PackageConfig], tuple[SetupField, ...]]:
        """The config model the instance's package declares, and the package's setup fields."""
        provenance = self._instance.manifest.package
        if provenance is None:
            raise PackageConfigNotFound(
                "The instance runs no package, so it has no package config."
            )
        package = load_package(provenance.id).package
        if package.config is None:
            raise PackageConfigNotFound(f'Package "{provenance.id}" declares no config.')
        return package.config, package.setup_fields

    def _write(self, file: ConfigFile, content: bytes, read: FileHash) -> None:
        with recorded_change(self._instance, file, ConfigActor.APP):
            _write_over(self._instance.path / file, content, read)


def _permissions_result(policy: GatePolicy, read: FileHash) -> PermissionsResult:
    return PermissionsResult(
        mode=policy.mode,
        ceiling=policy.ceiling,
        tools=dict(policy.tools),
        bash=BashPermissions(
            deny=[
                DenyPattern(pattern=pattern, shipped=pattern in SHIPPED_BASH_DENY)
                for pattern in policy.bash.denylist
            ],
            ask=list(policy.bash.ask),
        ),
        hash=read,
    )
