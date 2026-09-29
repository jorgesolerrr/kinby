"""Read and write an instance's configuration files for clients, each read carrying its hash."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from contextlib import AbstractContextManager
from pathlib import Path
from tempfile import TemporaryDirectory

from kinby.contracts import (
    ConfigActor,
    ConfigFile,
    ConfigHistoryCommand,
    ConfigHistoryResult,
    FileHash,
    PromptGetCommand,
    PromptName,
    PromptResult,
    PromptSetCommand,
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
from kinby.core.errors import InvalidConfig, SkillNotFound, StaleWrite
from kinby.instance import Instance
from kinby.instance.config_changes import ConfigChangeLog, recorded_change
from kinby.instance.layout import RECAP_NAME, SKILL_FILE, SKILLS_DIR, SYSTEM_NAME
from kinby.instance.permissions import load_permissions
from kinby.instance.recap import DEFAULT_RECAP_LENS
from kinby.plugins.core import core_tools
from kinby.plugins.instance_tools import validate_name, write_skill
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.skills import load_skill_tiers, load_skills, lower_tier_skill

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


def _directory_files(directory: Path) -> dict[str, bytes]:
    """Every file under *directory* by its path in it, sorted. Empty when there is no directory."""
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _directory_hash(files: dict[str, bytes]) -> FileHash:
    """Hash each file's path and bytes in order, so a missing directory hashes as empty bytes."""
    digest = hashlib.sha256()
    for name, content in files.items():
        digest.update(f"{name}\0{len(content)}\0".encode())
        digest.update(content)
    return FileHash(digest.hexdigest())


def _skill_result(directory: Path) -> SkillResult:
    files = _directory_files(directory)
    return SkillResult(
        content=files[SKILL_FILE].decode("utf-8"),
        files=[name for name in files if name != SKILL_FILE],
        hash=_directory_hash(files),
    )


def _check_unchanged(directory: Path, read: FileHash) -> None:
    if _directory_hash(_directory_files(directory)) != read:
        raise StaleWrite(f"{SKILLS_DIR}/{directory.name} changed since it was read. Read it again.")


def _write_over(path: Path, content: bytes, read: FileHash) -> None:
    """Replace the file the client read, staging beside it so no reader sees half a file."""
    if _file_hash(_read_bytes(path) or b"") != read:
        raise StaleWrite(f"{path.name} changed since it was read. Read it again.")
    staging = path.with_name(f".{path.name}.staging")
    staging.write_bytes(content)
    staging.replace(path)


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

    def _write(self, file: ConfigFile, content: bytes, read: FileHash) -> None:
        with recorded_change(self._instance, file, ConfigActor.APP):
            _write_over(self._instance.path / file, content, read)

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
        return recorded_change(self._instance, ConfigFile(f"{SKILLS_DIR}/{name}"), ConfigActor.APP)

    def _write_skill(self, command: SkillWriteCommand) -> SkillResult:
        directory = self._skill_directory(command.name)
        if command.hash is None:
            if directory.exists():
                raise StaleWrite(f"{SKILLS_DIR}/{command.name} already exists. Read it again.")
        else:
            if not directory.exists():
                self._refuse_read_only(command.name)
            _check_unchanged(directory, command.hash)
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
        _check_unchanged(directory, command.hash)
        with self._recorded_skill(command.name):
            shutil.rmtree(directory)
