"""Expose routine and skill files as gated core tools."""

import asyncio
import json
import re
import shutil
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from cronsim import CronSim

from kinby.contracts import ConfigActor, ConfigFile, CronSchedule, RoutineName, SkillName
from kinby.instance import Instance
from kinby.instance.config_changes import recorded_change
from kinby.instance.layout import (
    ROUTINE_CODE_FILE,
    ROUTINE_FILE,
    ROUTINES_DIR,
    SKILL_FILE,
    SKILLS_DIR,
)
from kinby.instance.permissions import load_permissions
from kinby.plugins.routines import (
    load_routine,
    load_routine_file,
    load_routines,
    set_routine_enabled,
)
from kinby.plugins.skills import describe_skill_shadow, load_skill_file
from kinby.plugins.tools import Tool, ToolContext, tool

_INSTANCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def validate_name(name: str) -> None:
    """Refuse a routine or skill name that is not one safe directory name."""
    if _INSTANCE_NAME.fullmatch(name) is None:
        raise ValueError(
            "A name must contain only letters, digits, hyphens, and underscores, "
            "and start with a letter or digit."
        )


def _next_firing(schedule: CronSchedule, instance: Instance) -> datetime:
    zone = instance.manifest.routines.timezone
    return next(CronSim(schedule, datetime.now(zone)))


def write_skill(instance: Instance, name: str, content: str) -> None:
    """Write an instance skill's SKILL.md once the skill loader accepts it.

    The skill is staged beside the instance and renamed into place, keeping its other files.
    """
    validate_name(name)
    skills = instance.path / SKILLS_DIR
    skills.mkdir(parents=True, exist_ok=True)
    target = skills / name
    with TemporaryDirectory(prefix=".skill-", dir=instance.path) as temporary:
        staged = Path(temporary) / name
        if target.is_dir():
            shutil.copytree(target, staged)
        else:
            staged.mkdir()
        skill_path = staged / SKILL_FILE
        skill_path.write_text(content, encoding="utf-8")
        skill = load_skill_file(skill_path)
        if skill.name != name:
            raise ValueError(
                f'Skill frontmatter name "{skill.name}" must match directory name "{name}".'
            )
        previous = Path(temporary) / ".previous"
        if target.exists():
            target.rename(previous)
        try:
            staged.rename(target)
        except Exception:
            if previous.exists():
                previous.rename(target)
            raise


def _recorded(context: ToolContext, file: str) -> AbstractContextManager[None]:
    """Record the change an instance tool makes as the agent's, in the turn it runs in."""
    return recorded_change(
        context.instance,
        ConfigFile(file),
        ConfigActor.AGENT,
        thread_id=context.thread_id,
        turn_id=context.turn_id,
    )


def instance_tools(instance: Instance) -> tuple[Tool, ...]:
    """Build the core tools that read and write one instance."""

    @tool(write=False)
    def routine_list(context: ToolContext) -> str:
        """List each routine's configuration, signal state, and pending deliveries."""
        from kinby.core.events import EventLog
        from kinby.core.routine_history import routine_history

        routines, warnings = load_routines(context.instance)
        histories = routine_history(
            EventLog(context.instance.manifest.state_dir).all_events()
        ).routines
        records: dict[str, dict[str, object]] = {}
        for routine in routines:
            history = histories.get(routine.name)
            records[routine.name] = {
                "name": routine.name,
                "description": routine.description,
                "schedule": routine.schedule,
                "enabled": routine.enabled,
                "mode": routine.mode.value,
                "signal": routine.signal is not None,
                "pending": len(history.pending) if history is not None else 0,
            }
        for warning in warnings:
            name = Path(warning.sources[0]).parent.name
            records[name] = {"name": name, "warning": warning.message}
        return json.dumps(
            [records[name] for name in sorted(records)],
            ensure_ascii=False,
        )

    @tool(write=False)
    def routine_read(name: str, context: ToolContext) -> str:
        """Read a routine's complete ROUTINE.md and optional run.py."""
        validate_name(name)
        directory = context.instance.path / ROUTINES_DIR / name
        routine_path = directory / ROUTINE_FILE
        if not routine_path.is_file():
            raise LookupError(f'Routine "{name}" was not found.')
        files = {ROUTINE_FILE: routine_path.read_text(encoding="utf-8")}
        code_path = directory / ROUTINE_CODE_FILE
        if code_path.is_file():
            files[ROUTINE_CODE_FILE] = code_path.read_text(encoding="utf-8")
        return json.dumps(files, ensure_ascii=False)

    def _write_routine(
        name: str,
        content: str,
        context: ToolContext,
        code: str | None = None,
    ) -> str:
        """Create or replace a routine from its complete ROUTINE.md and optional run.py."""
        validate_name(name)
        routines = context.instance.path / ROUTINES_DIR
        routines.mkdir(parents=True, exist_ok=True)
        target = routines / name
        with (
            _recorded(context, f"{ROUTINES_DIR}/{name}"),
            TemporaryDirectory(prefix=".routine-", dir=context.instance.path) as temporary,
        ):
            staged = Path(temporary) / name
            if target.is_dir():
                shutil.copytree(target, staged)
            else:
                staged.mkdir()
            routine_path = staged / ROUTINE_FILE
            routine_path.write_text(content, encoding="utf-8")
            if code is not None:
                (staged / ROUTINE_CODE_FILE).write_text(code, encoding="utf-8")
            routine = load_routine_file(
                routine_path,
                load_permissions(context.instance),
                context.instance,
            )
            previous = Path(temporary) / ".previous"
            if target.exists():
                target.rename(previous)
            try:
                staged.rename(target)
            except Exception:
                if previous.exists():
                    previous.rename(target)
                raise

        result = f"Wrote routines/{name}/ROUTINE.md."
        if routine.enabled and routine.schedule is not None:
            next_firing = _next_firing(routine.schedule, context.instance)
            result = f"{result} Next firing: {next_firing.isoformat()}."
        if routine.signal is not None and context.instance.manifest.serve is not None:
            result = f"{result} Signal path: /signals/{name}."
        if not routine.enabled:
            result = f"{result} Status: disabled."
        return result

    @tool(write=True)
    async def routine_write(
        name: str,
        content: str,
        context: ToolContext,
        code: str | None = None,
    ) -> str:
        """Create or replace a routine from its complete ROUTINE.md and optional run.py."""
        async with context.instance.routine_lock:
            return await asyncio.to_thread(_write_routine, name, content, context, code)

    def _set_routine_enabled(
        name: str,
        *,
        enabled: bool,
        context: ToolContext,
    ) -> str:
        validate_name(name)
        routine = load_routine(context.instance, RoutineName(name))
        if routine is None:
            raise LookupError(f'Routine "{name}" was not found.')
        with _recorded(context, f"{ROUTINES_DIR}/{name}"):
            set_routine_enabled(routine, enabled=enabled)
        if not enabled:
            return f"Routine {name} disabled."
        result = f"Enabled routine {name}."
        if routine.schedule is not None:
            next_firing = _next_firing(routine.schedule, context.instance)
            result = f"{result} Next firing: {next_firing.isoformat()}."
        return result

    @tool(write=True)
    async def routine_set_enabled(
        name: str,
        *,
        enabled: bool,
        context: ToolContext,
    ) -> str:
        """Enable or disable a routine."""
        async with context.instance.routine_lock:
            return await asyncio.to_thread(
                _set_routine_enabled,
                name,
                enabled=enabled,
                context=context,
            )

    def _delete_routine(name: str, context: ToolContext) -> str:
        validate_name(name)
        from kinby.core.events import EventLog
        from kinby.core.routine_history import routine_history

        target = context.instance.path / ROUTINES_DIR / name
        if not target.is_dir():
            raise LookupError(f'Routine "{name}" was not found.')
        histories = routine_history(
            EventLog(context.instance.manifest.state_dir).all_events()
        ).routines
        history = histories.get(RoutineName(name))
        pending = len(history.pending) if history is not None else 0
        if pending:
            noun = "delivery" if pending == 1 else "deliveries"
            raise ValueError(
                f'Routine "{name}" has {pending} pending {noun} and cannot be deleted.'
            )
        with _recorded(context, f"{ROUTINES_DIR}/{name}"):
            shutil.rmtree(target)
        return f"Deleted routines/{name}/."

    @tool(write=True)
    async def routine_delete(name: str, context: ToolContext) -> str:
        """Delete a routine when it has no pending deliveries."""
        async with context.instance.routine_lock:
            return await asyncio.to_thread(_delete_routine, name, context)

    def _write_skill(name: str, content: str, context: ToolContext) -> str:
        validate_name(name)
        shadow = describe_skill_shadow(context.instance, SkillName(name))
        with _recorded(context, f"{SKILLS_DIR}/{name}"):
            write_skill(context.instance, name, content)
        result = f"Wrote skills/{name}/SKILL.md."
        if shadow is not None:
            result = f"{result} Shadows {shadow}."
        return result

    @tool(write=True)
    async def skill_write(name: str, content: str, context: ToolContext) -> str:
        """Write a SKILL.md with required name and description frontmatter keys.

        Its body is the instructions.
        """
        async with context.instance.skill_lock:
            return await asyncio.to_thread(_write_skill, name, content, context)

    def _delete_skill(name: str, context: ToolContext) -> str:
        validate_name(name)
        target = context.instance.path / SKILLS_DIR / name
        if not target.is_dir():
            shadow = describe_skill_shadow(context.instance, SkillName(name))
            if shadow is not None:
                raise LookupError(
                    f'Skill "{name}" exists only as a {shadow}; '
                    "skill_delete removes instance skills only."
                )
            raise LookupError(f'Skill "{name}" was not found in the instance.')
        with _recorded(context, f"{SKILLS_DIR}/{name}"):
            shutil.rmtree(target)
        return f"Deleted skills/{name}/."

    @tool(write=True)
    async def skill_delete(name: str, context: ToolContext) -> str:
        """Delete a skill from the instance tier."""
        async with context.instance.skill_lock:
            return await asyncio.to_thread(_delete_skill, name, context)

    return (
        routine_list,
        routine_read,
        routine_write,
        routine_set_enabled,
        routine_delete,
        skill_write,
        skill_delete,
    )
