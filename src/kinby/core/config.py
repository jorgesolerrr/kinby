"""Read and write an instance's configuration files for clients, each read carrying its hash."""

from __future__ import annotations

import asyncio
import math
import os
import shutil
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

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
    ManifestGetCommand,
    ManifestResult,
    ManifestSetCommand,
    ModelChoice,
    PackageConfigGetCommand,
    PackageConfigResult,
    PackageConfigSetCommand,
    PermissionsGetCommand,
    PermissionsResult,
    PermissionsSetCommand,
    PriceSource,
    ProfileGetCommand,
    ProfileResult,
    ProfileSetCommand,
    PromptGetCommand,
    PromptName,
    PromptResult,
    PromptSetCommand,
    RecreateReason,
    RoutineDeleteCommand,
    RoutineDeleteResult,
    RoutineFile,
    RoutineName,
    RoutineReadCommand,
    RoutineRenameCommand,
    RoutineSetEnabledCommand,
    RoutineWriteCommand,
    SkillCustomizeCommand,
    SkillDeleteCommand,
    SkillListCommand,
    SkillListResult,
    SkillName,
    SkillReadCommand,
    SkillResult,
    SkillSummary,
    SkillTier,
    SkillWriteCommand,
    ToolListCommand,
    ToolListResult,
    ToolRule,
    ToolSummary,
)
from kinby.core.errors import (
    InvalidConfig,
    PackageConfigNotFound,
    RoutineNotFound,
    RoutineRefused,
    SkillNotFound,
    StaleWrite,
)
from kinby.core.pricing import SHIPPED_PRICES
from kinby.instance import Instance, api_key_variable
from kinby.instance.config_changes import (
    ConfigChangeLog,
    directory_hash,
    file_hash,
    recorded_change,
    recorded_changes,
)
from kinby.instance.layout import (
    MANIFEST_NAME,
    MEMORY_DIR,
    PERMISSIONS_NAME,
    PROFILE_NAME,
    RECAP_NAME,
    ROUTINE_FILE,
    SKILL_FILE,
    SKILLS_DIR,
    SYSTEM_NAME,
)
from kinby.instance.manifest import (
    edited_manifest,
    manifest_field_errors,
    offered_values,
    parse_manifest,
)
from kinby.instance.permissions import (
    SHIPPED_BASH_DENY,
    SHIPPED_POLICY,
    BashPolicy,
    GatePolicy,
    bash_regex_errors,
    edited_permissions,
    exceeds_ceiling,
    load_permissions,
    parse_permissions,
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
from kinby.plugins.core import core_tools
from kinby.plugins.instance_tools import (
    delete_routine,
    enable_routine,
    refuse_pending,
    routine_config_file,
    validate_name,
    write_routine,
    write_skill,
)
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.skills import load_skill_tiers, load_skills, lower_tier_skill

_PROMPT_FILES = {
    PromptName.BEHAVIOR: ConfigFile(SYSTEM_NAME),
    PromptName.RECAP: ConfigFile(RECAP_NAME),
}
#: What each prompt reads as without its file. kinby ships no behavior prompt of its own.
_SHIPPED_PROMPTS = {PromptName.BEHAVIOR: "", PromptName.RECAP: DEFAULT_RECAP_LENS}
_PROFILE_FILE = ConfigFile(f"{MEMORY_DIR}/{PROFILE_NAME}")


def _check_unchanged(instance_path: Path, file: ConfigFile, read: FileHash | None) -> None:
    """Refuse a write over a directory changed since the client read it, or over one it creates."""
    directory = instance_path / file
    if read is None and directory.exists():
        raise StaleWrite(f"{file} already exists. Read it first.")
    if read is not None and directory_hash(directory) != read:
        raise StaleWrite(f"{file} changed since it was read. Read it again.")


@contextmanager
def _refusals() -> Iterator[None]:
    """Report the instance tools' refusals as the contract errors a client reads."""
    try:
        yield
    except LookupError as exc:
        raise RoutineNotFound(str(exc)) from exc
    except ValueError as exc:
        raise RoutineRefused(str(exc)) from exc


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _directory_files(directory: Path) -> dict[str, bytes]:
    """Every file under *directory* by its path in it, sorted. Empty when there is no directory."""
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _skill_result(directory: Path) -> SkillResult:
    files = _directory_files(directory)
    return SkillResult(
        content=files[SKILL_FILE].decode("utf-8"),
        files=[name for name in files if name != SKILL_FILE],
        hash=directory_hash(directory),
    )


def _write_over(path: Path, content: bytes, read: FileHash) -> None:
    """Replace the file the client read, staging beside it so no reader sees half a file."""
    if file_hash(_read_bytes(path) or b"") != read:
        raise StaleWrite(f"{path.name} changed since it was read. Read it again.")
    path.parent.mkdir(exist_ok=True)
    staging = path.with_name(f".{path.name}.staging")
    staging.write_bytes(content)
    staging.replace(path)


def package_config_hash(instance: Instance) -> FileHash | None:
    """The hash of a packaged instance's package.yaml. None for a vanilla instance."""
    if instance.manifest.package is None:
        return None
    return file_hash(_read_bytes(instance.path / PACKAGE_CONFIG_NAME) or b"")


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
        hash=file_hash(content),
    )


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
                content=_SHIPPED_PROMPTS[command.name], hash=file_hash(b""), default=True
            )
        return PromptResult(content=content.decode("utf-8"), hash=file_hash(content), default=False)

    async def set_prompt(self, command: PromptSetCommand) -> PromptResult:
        content = command.content.encode("utf-8")
        async with self._instance.config_lock:
            await asyncio.to_thread(self._write, _PROMPT_FILES[command.name], content, command.hash)
        return PromptResult(content=command.content, hash=file_hash(content), default=False)

    async def get_profile(self, command: ProfileGetCommand) -> ProfileResult:
        return _profile_result(_read_bytes(self._instance.path / _PROFILE_FILE) or b"")

    async def set_profile(self, command: ProfileSetCommand) -> ProfileResult:
        """Write the profile. The agent reads it into the system prompt at its next turn."""
        content = command.text.encode("utf-8")
        async with self._instance.config_lock:
            await asyncio.to_thread(self._write, _PROFILE_FILE, content, command.hash)
        return _profile_result(content)

    async def read_routine(self, command: RoutineReadCommand) -> RoutineFile:
        async with self._instance.routine_lock:
            return await asyncio.to_thread(self._read_routine, command.name)

    async def write_routine(self, command: RoutineWriteCommand) -> RoutineFile:
        async with self._instance.routine_lock:
            return await asyncio.to_thread(self._write_routine, command)

    async def set_routine_enabled(self, command: RoutineSetEnabledCommand) -> RoutineFile:
        async with self._instance.routine_lock:
            return await asyncio.to_thread(self._set_routine_enabled, command)

    async def delete_routine(self, command: RoutineDeleteCommand) -> RoutineDeleteResult:
        async with self._instance.routine_lock:
            await asyncio.to_thread(self._delete_routine, command)
        return RoutineDeleteResult()

    async def rename_routine(self, command: RoutineRenameCommand) -> RoutineFile:
        async with self._instance.routine_lock:
            return await asyncio.to_thread(self._rename_routine, command)

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

    async def get_permissions(self, command: PermissionsGetCommand) -> PermissionsResult:
        content = _read_bytes(self._instance.path / PERMISSIONS_NAME)
        if content is None:
            return _permissions_result(SHIPPED_POLICY, file_hash(b""))
        return _permissions_result(parse_permissions(content), file_hash(content))

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
        path = self._instance.path / PERMISSIONS_NAME
        async with self._instance.config_lock:
            text = (_read_bytes(path) or b"").decode("utf-8")
            content = edited_permissions(text, policy).encode("utf-8")
            await asyncio.to_thread(
                self._write, ConfigFile(PERMISSIONS_NAME), content, command.hash
            )
        return _permissions_result(policy, file_hash(content))

    async def get_package_config(self, command: PackageConfigGetCommand) -> PackageConfigResult:
        config, _ = self._package_config()
        content = _read_bytes(self._instance.path / PACKAGE_CONFIG_NAME) or b""
        values = yaml.safe_load(content) or {}
        return PackageConfigResult(
            schema=config.model_json_schema(),
            values=values if isinstance(values, dict) else {},
            hash=file_hash(content),
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
            hash=file_hash(content),
        )

    async def list_skills(self, command: SkillListCommand) -> SkillListResult:
        tiered, warnings = load_skill_tiers(self._instance)
        winners: dict[SkillName, SkillTier] = {}
        for each in tiered:
            winners.setdefault(each.skill.name, each.tier)
        return SkillListResult(
            skills=[
                SkillSummary(
                    name=each.skill.name,
                    tier=each.tier,
                    description=each.skill.description,
                    source=each.origin,
                    shadowed_by=None
                    if winners[each.skill.name] is each.tier
                    else winners[each.skill.name],
                )
                # The sort is stable, so each name's skills stay in tier order.
                for each in sorted(tiered, key=lambda each: each.skill.name)
            ],
            warnings=warnings,
        )

    async def read_skill(self, command: SkillReadCommand) -> SkillResult:
        tiered, _ = load_skill_tiers(self._instance)
        found = next(
            (
                each
                for each in tiered
                if each.skill.name == command.name and each.tier is command.tier
            ),
            None,
        )
        if found is None:
            raise SkillNotFound(f'There is no {command.tier} skill "{command.name}".')
        return _skill_result(found.skill.source.parent)

    async def write_skill(self, command: SkillWriteCommand) -> SkillResult:
        async with self._instance.skill_lock:
            return await asyncio.to_thread(self._write_skill, command)

    async def customize_skill(self, command: SkillCustomizeCommand) -> SkillResult:
        async with self._instance.skill_lock:
            return await asyncio.to_thread(self._customize_skill, command.name)

    async def delete_skill(self, command: SkillDeleteCommand) -> SkillListResult:
        async with self._instance.skill_lock:
            await asyncio.to_thread(self._delete_skill, command)
        return await self.list_skills(SkillListCommand())

    async def list_tools(self, command: ToolListCommand) -> ToolListResult:
        skills, _ = load_skills(self._instance)
        registry = ToolRegistry(
            self._instance.path, defaults=self._instance.manifest.tools.defaults
        )
        discovered, tool_warnings = registry.refresh()
        core = core_tools(self._instance, skills)
        tools, core_warnings = discovered.with_core(*core)
        core_names = {each.name for each in core}
        rules = load_permissions(self._instance).tools
        return ToolListResult(
            tools=[
                ToolSummary(
                    name=each.name,
                    source="core" if each.name in core_names else registry.origin(each),
                    write=each.write,
                    rule=ToolRule(rules[each.name]) if each.name in rules else ToolRule.MODE,
                )
                for each in tools.tools
            ],
            warnings=(*tool_warnings, *core_warnings),
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
        with self._recorded(file):
            _write_over(self._instance.path / file, content, read)

    def _recorded(self, file: ConfigFile) -> AbstractContextManager[None]:
        return recorded_change(self._instance, file, ConfigActor.APP)

    def _write_routine(self, command: RoutineWriteCommand) -> RoutineFile:
        with _refusals():
            validate_name(command.name)
            _check_unchanged(self._instance.path, routine_config_file(command.name), command.hash)
            write_routine(self._instance, command.name, command.content, self._recorded)
        return self._read_routine(command.name)

    def _set_routine_enabled(self, command: RoutineSetEnabledCommand) -> RoutineFile:
        with _refusals():
            enable_routine(self._instance, command.name, self._recorded, enabled=command.enabled)
        return self._read_routine(command.name)

    def _delete_routine(self, command: RoutineDeleteCommand) -> None:
        with _refusals():
            validate_name(command.name)
            _check_unchanged(self._instance.path, routine_config_file(command.name), command.hash)
            delete_routine(self._instance, command.name, self._recorded)

    def _rename_routine(self, command: RoutineRenameCommand) -> RoutineFile:
        with _refusals():
            validate_name(command.name)
        try:
            validate_name(command.new_name)
        except ValueError as exc:
            raise InvalidConfig({"new_name": str(exc)}) from exc
        old, new = routine_config_file(command.name), routine_config_file(command.new_name)
        _check_unchanged(self._instance.path, old, command.hash)
        if not (self._instance.path / old).is_dir():
            raise RoutineNotFound(f'Routine "{command.name}" was not found.')
        refuse_pending(self._instance, command.name, "renamed")
        if (self._instance.path / new).exists():
            raise InvalidConfig({"new_name": f'Routine "{command.new_name}" already exists.'})
        with recorded_changes(self._instance, (old, new), ConfigActor.APP):
            # One rename, so the scheduler's next read finds the old directory or the new one.
            (self._instance.path / old).rename(self._instance.path / new)
        return self._read_routine(command.new_name)

    def _read_routine(self, name: RoutineName) -> RoutineFile:
        with _refusals():
            validate_name(name)
        directory = self._instance.path / routine_config_file(name)
        content = _read_bytes(directory / ROUTINE_FILE)
        if content is None:
            raise RoutineNotFound(f'Routine "{name}" was not found.')
        return RoutineFile(
            name=name, content=content.decode("utf-8"), hash=directory_hash(directory)
        )

    def _skill_directory(self, name: SkillName) -> Path:
        try:
            validate_name(name)
        except ValueError as exc:
            raise InvalidConfig({"name": str(exc)}) from exc
        return self._instance.path / SKILLS_DIR / name

    def _refuse_read_only(self, name: SkillName) -> None:
        """Refuse a change to a package or workspace skill the instance has no copy of."""
        lower = lower_tier_skill(self._instance, name)
        if lower is not None:
            raise InvalidConfig(
                {
                    "name": f'"{name}" is a {lower.tier} skill, which is read-only. '
                    "Customize it to edit a copy in the instance."
                }
            )

    def _recorded_skill(self, name: SkillName) -> AbstractContextManager[None]:
        return self._recorded(_skill_config_file(name))

    def _write_skill(self, command: SkillWriteCommand) -> SkillResult:
        directory = self._skill_directory(command.name)
        if command.hash is not None and not directory.exists():
            self._refuse_read_only(command.name)
        _check_unchanged(self._instance.path, _skill_config_file(command.name), command.hash)
        with self._recorded_skill(command.name):
            try:
                write_skill(self._instance, command.name, command.content)
            except ValueError as exc:
                raise InvalidConfig({"content": str(exc)}) from exc
        return _skill_result(directory)

    def _customize_skill(self, name: SkillName) -> SkillResult:
        directory = self._skill_directory(name)
        if directory.exists():
            raise StaleWrite(f"{SKILLS_DIR}/{name} is already in the instance. Read it again.")
        lower = lower_tier_skill(self._instance, name)
        if lower is None:
            raise SkillNotFound(f'There is no package or workspace skill "{name}".')
        directory.parent.mkdir(parents=True, exist_ok=True)
        with (
            self._recorded_skill(name),
            TemporaryDirectory(prefix=".skill-", dir=self._instance.path) as temporary,
        ):
            staged = Path(temporary) / name
            shutil.copytree(lower.skill.source.parent, staged)
            staged.rename(directory)
        return _skill_result(directory)

    def _delete_skill(self, command: SkillDeleteCommand) -> None:
        directory = self._skill_directory(command.name)
        if not directory.is_dir():
            self._refuse_read_only(command.name)
            raise SkillNotFound(f'The instance has no skill "{command.name}".')
        _check_unchanged(self._instance.path, _skill_config_file(command.name), command.hash)
        with self._recorded_skill(command.name):
            shutil.rmtree(directory)


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


def _profile_result(content: bytes) -> ProfileResult:
    text = content.decode("utf-8")
    return ProfileResult(text=text, hash=file_hash(content), tokens=math.ceil(len(text) / 4))


def _skill_config_file(name: SkillName) -> ConfigFile:
    return ConfigFile(f"{SKILLS_DIR}/{name}")
