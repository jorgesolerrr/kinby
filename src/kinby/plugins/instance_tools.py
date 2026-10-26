"""Expose routine and skill files, repository commands, and instance code as gated core tools."""

import asyncio
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from cronsim import CronSim
from pydantic import TypeAdapter, ValidationError

from kinby.contracts import (
    CommandListName,
    ConfigActor,
    ConfigFile,
    CronSchedule,
    RoutineName,
    SkillName,
)
from kinby.instance import Instance
from kinby.instance.code import declared_secrets
from kinby.instance.config_changes import recorded_change
from kinby.instance.layout import (
    CODE_DIR,
    CODE_PROJECT_NAME,
    MANIFEST_NAME,
    ROUTINE_CODE_FILE,
    ROUTINE_FILE,
    ROUTINES_DIR,
    SKILL_FILE,
    SKILLS_DIR,
)
from kinby.instance.manifest import with_repository_commands
from kinby.instance.permissions import load_permissions
from kinby.instance.routine_grants import RoutineGrants
from kinby.plugins.defaults.shell import run_capped
from kinby.plugins.routines import (
    Routine,
    load_routine,
    load_routine_file,
    load_routines,
    set_routine_enabled,
)
from kinby.plugins.skills import describe_skill_shadow, load_skill_file
from kinby.plugins.tools import Tool, ToolContext, tool

_INSTANCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_COMMAND_LIST_NAME = TypeAdapter(CommandListName)
_SECRET_NAME = re.compile("KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
_CODE_VENV = ".venv"
_CODE_PROJECT = f"""[project]
name = "instance-code"
version = "0.0.0"
requires-python = ">={sys.version_info.major}.{sys.version_info.minor}"
dependencies = []

# A variable named like a secret reaches a script only when this list names it.
[tool.kinby]
secrets = []
"""

#: Records what its body changes in one configuration file or directory, as one config change.
type Recorder = Callable[[ConfigFile], AbstractContextManager[None]]


def validate_name(name: str) -> None:
    """Refuse a routine or skill name that is not one safe directory name."""
    if _INSTANCE_NAME.fullmatch(name) is None:
        raise ValueError(
            "A name must contain only letters, digits, hyphens, and underscores, "
            "and start with a letter or digit."
        )


def write_repository_commands(
    instance: Instance, name: str, commands: list[str], recorded: Recorder
) -> None:
    """Set the instance's list of repository commands *name* to *commands*, replacing the whole
    list. Hold the config lock."""
    try:
        _COMMAND_LIST_NAME.validate_python(name)
    except ValidationError as exc:
        raise ValueError(
            "A list name must contain only lowercase letters, digits, hyphens, and underscores, "
            "and start with a letter or digit."
        ) from exc
    for number, command in enumerate(commands, start=1):
        if not command.strip():
            raise ValueError(f"Command {number} is empty. Give each command as one line to run.")
    path = instance.path / MANIFEST_NAME
    edited = with_repository_commands(path.read_text(encoding="utf-8"), name, commands)
    with recorded(ConfigFile(MANIFEST_NAME)):
        staging = path.with_name(f".{path.name}.staging")
        staging.write_text(edited, encoding="utf-8")
        staging.replace(path)


def _next_firing(schedule: CronSchedule, instance: Instance) -> datetime:
    zone = instance.manifest.routines.timezone
    return next(CronSim(schedule, datetime.now(zone)))


def write_skill(skills: Path, name: str, content: str, staging: Path) -> None:
    """Write the SKILL.md of the skill *name* in the folder *skills* once the skill loader
    accepts it.

    The skill is staged in *staging* and renamed into place, keeping its other files.
    """
    validate_name(name)
    skills.mkdir(parents=True, exist_ok=True)
    target = skills / name
    with TemporaryDirectory(prefix=".skill-", dir=staging) as temporary:
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


def routine_config_file(name: RoutineName) -> ConfigFile:
    """A routine's directory, as its config changes name it."""
    return ConfigFile(f"{ROUTINES_DIR}/{name}")


def _recorded(context: ToolContext, file: ConfigFile) -> AbstractContextManager[None]:
    """Record the change an instance tool makes as the agent's, in the turn it runs in."""
    return recorded_change(
        context.instance,
        file,
        ConfigActor.AGENT,
        thread_id=context.thread_id,
        turn_id=context.turn_id,
    )


def write_routine(
    instance: Instance,
    name: RoutineName,
    content: str,
    recorded: Recorder,
    code: str | None = None,
    *,
    code_step_allowed: bool = True,
) -> Routine:
    """Create or replace a routine once its staged copy loads, revoking its grants. Hold the
    routine lock."""
    validate_name(name)
    routines = instance.path / ROUTINES_DIR
    routines.mkdir(parents=True, exist_ok=True)
    target = routines / name
    with (
        recorded(routine_config_file(name)),
        TemporaryDirectory(prefix=".routine-", dir=instance.path) as temporary,
    ):
        staged = Path(temporary) / name
        if target.is_dir():
            shutil.copytree(target, staged)
        else:
            staged.mkdir()
        routine_path = staged / ROUTINE_FILE
        routine_path.write_text(content, encoding="utf-8")
        # Loading a run.py runs it, so a refused one is never written.
        if code is not None and code_step_allowed:
            (staged / ROUTINE_CODE_FILE).write_text(code, encoding="utf-8")
        routine = load_routine_file(routine_path, load_permissions(instance), instance)
        if not code_step_allowed and (code is not None or routine.code_step is not None):
            raise ValueError(
                "A dreaming stage cannot write a routine with a code step, run or run.py."
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
    RoutineGrants(instance.manifest.state_dir).clear(name)
    return routine


def enable_routine(
    instance: Instance, name: RoutineName, recorded: Recorder, *, enabled: bool
) -> Routine:
    """Change only a routine's enabled line, and revoke its grants. Hold the routine lock."""
    validate_name(name)
    routine = load_routine(instance, name)
    if routine is None:
        raise LookupError(f'Routine "{name}" was not found.')
    with recorded(routine_config_file(name)):
        set_routine_enabled(routine, enabled=enabled)
    RoutineGrants(instance.manifest.state_dir).clear(name)
    return routine


def refuse_pending(
    instance: Instance, name: RoutineName, change: Literal["deleted", "renamed"]
) -> None:
    """Refuse a *change* that would drop the deliveries waiting on a routine."""
    from kinby.core.errors import RoutinePending
    from kinby.core.events import EventLog
    from kinby.core.routine_history import routine_history

    histories = routine_history(EventLog(instance.manifest.state_dir).all_events()).routines
    history = histories.get(name)
    pending = len(history.pending) if history is not None else 0
    if pending:
        noun = "delivery" if pending == 1 else "deliveries"
        raise RoutinePending(
            f'Routine "{name}" has {pending} pending {noun} and cannot be {change}.'
        )


def delete_routine(instance: Instance, name: RoutineName, recorded: Recorder) -> None:
    """Delete a routine and its grants unless deliveries wait on it. Hold the routine lock."""
    validate_name(name)
    target = instance.path / ROUTINES_DIR / name
    if not target.is_dir():
        raise LookupError(f'Routine "{name}" was not found.')
    refuse_pending(instance, name, "deleted")
    with recorded(routine_config_file(name)):
        shutil.rmtree(target)
    RoutineGrants(instance.manifest.state_dir).clear(name)


def _code_file(instance: Instance, path: str) -> ConfigFile:
    """The file *path* names in the instance code, refused when it falls outside."""
    code = (instance.path / CODE_DIR).resolve()
    target = (code / path).resolve()
    if target == code or not target.is_relative_to(code):
        raise ValueError(f'"{path}" is not a path inside the instance code, {CODE_DIR}/.')
    return ConfigFile(f"{CODE_DIR}/{target.relative_to(code).as_posix()}")


def _code_project(context: ToolContext) -> Path:
    """The instance code's directory, given a pyproject.toml first when it has none."""
    code = context.instance.path / CODE_DIR
    project = code / CODE_PROJECT_NAME
    if not project.is_file():
        code.mkdir(parents=True, exist_ok=True)
        with _recorded(context, ConfigFile(f"{CODE_DIR}/{CODE_PROJECT_NAME}")):
            project.write_text(_CODE_PROJECT, encoding="utf-8")
    return code


def _uv(context: ToolContext, command: str, *arguments: str) -> None:
    code = _code_project(context)
    # Keep the venv where code_run looks for it, even when kinby's own environment points uv
    # elsewhere.
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(code / _CODE_VENV)}
    returncode, output = run_capped(
        ("uv", command, "--project", str(code), *arguments),
        cwd=code,
        timeout_seconds=context.instance.manifest.tools.bash_timeout_seconds,
        label=f"uv {command}",
        env=env,
    )
    if returncode:
        raise RuntimeError(output)


def _write_code(path: str, content: str, context: ToolContext) -> str:
    file = _code_file(context.instance, path)
    target = context.instance.path / file
    with _recorded(context, file):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return f"Wrote {file}."


def _delete_code(path: str, context: ToolContext) -> str:
    file = _code_file(context.instance, path)
    target = context.instance.path / file
    if not target.is_file():
        raise LookupError(f'"{path}" is not a file in the instance code.')
    with _recorded(context, file):
        target.unlink()
    return f"Deleted {file}."


def _add_package(name: str, context: ToolContext) -> str:
    with _recorded(context, ConfigFile(f"{CODE_DIR}/{CODE_PROJECT_NAME}")):
        _uv(context, "add", "--", name)
    return f"Added {name} to the instance code."


def _run_code(script: str, arguments: list[str], context: ToolContext) -> str:
    target = context.instance.path / _code_file(context.instance, script)
    if not target.is_file():
        raise LookupError(f'Script "{script}" was not found in the instance code.')
    code = _code_project(context)
    python = code / _CODE_VENV / "bin" / "python"
    if not python.exists():
        _uv(context, "sync")
    declared = set(declared_secrets(context.instance.path))
    env = {
        name: value
        for name, value in os.environ.items()
        if name in declared or _SECRET_NAME.search(name) is None
    }
    _, output = run_capped(
        (str(python), str(target), *arguments),
        cwd=code,
        timeout_seconds=context.instance.manifest.tools.bash_timeout_seconds,
        label=script,
        env=env,
    )
    return output


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

    @tool(write=True)
    async def routine_write(
        name: str,
        content: str,
        context: ToolContext,
        code: str | None = None,
    ) -> str:
        """Create or replace a routine from its complete ROUTINE.md and optional run.py."""
        recorded = partial(_recorded, context)
        async with context.instance.routine_lock:
            routine = await asyncio.to_thread(
                # Dreaming proposes no code, so the gate's trust in a write flag holds (ADR 0099).
                partial(write_routine, code_step_allowed=not context.dreaming_stage),
                context.instance,
                RoutineName(name),
                content,
                recorded,
                code,
            )
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
    async def routine_set_enabled(
        name: str,
        *,
        enabled: bool,
        context: ToolContext,
    ) -> str:
        """Enable or disable a routine."""
        recorded = partial(_recorded, context)
        async with context.instance.routine_lock:
            routine = await asyncio.to_thread(
                partial(enable_routine, enabled=enabled),
                context.instance,
                RoutineName(name),
                recorded,
            )
        if not enabled:
            return f"Routine {name} disabled."
        result = f"Enabled routine {name}."
        if routine.schedule is not None:
            next_firing = _next_firing(routine.schedule, context.instance)
            result = f"{result} Next firing: {next_firing.isoformat()}."
        return result

    @tool(write=True)
    async def routine_delete(name: str, context: ToolContext) -> str:
        """Delete a routine when it has no pending deliveries."""
        recorded = partial(_recorded, context)
        async with context.instance.routine_lock:
            await asyncio.to_thread(delete_routine, context.instance, RoutineName(name), recorded)
        return f"Deleted routines/{name}/."

    def _write_skill(name: str, content: str, context: ToolContext) -> str:
        validate_name(name)
        shadow = describe_skill_shadow(context.instance, SkillName(name))
        with _recorded(context, ConfigFile(f"{SKILLS_DIR}/{name}")):
            write_skill(context.instance.path / SKILLS_DIR, name, content, context.instance.path)
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
        with _recorded(context, ConfigFile(f"{SKILLS_DIR}/{name}")):
            shutil.rmtree(target)
        return f"Deleted skills/{name}/."

    @tool(write=True)
    async def skill_delete(name: str, context: ToolContext) -> str:
        """Delete a skill from the instance tier."""
        async with context.instance.skill_lock:
            return await asyncio.to_thread(_delete_skill, name, context)

    @tool(write=True)
    async def repository_commands_write(
        name: str, commands: list[str], context: ToolContext
    ) -> str:
        """Set one of the instance's lists of repository commands, such as setup or checks,
        replacing the whole list.

        A command step runs each command in the workspace without a shell. An empty list is a
        valid one.
        """
        recorded = partial(_recorded, context)
        async with context.instance.config_lock:
            await asyncio.to_thread(
                write_repository_commands, context.instance, name, commands, recorded
            )
        return f'Recorded the repository commands "{name}", {len(commands)} commands.'

    @tool(write=True)
    async def code_write(path: str, content: str, context: ToolContext) -> str:
        """Write a whole file in your instance code, the Python project under code/ that keeps
        your scripts and the packages they need.

        A secret a script needs reaches it only when `secrets` in the [tool.kinby] table of its
        pyproject.toml lists the variable. One the instance does not hold yet shows the user as
        pending setup.
        """
        return await asyncio.to_thread(_write_code, path, content, context)

    @tool(write=True)
    async def code_delete(path: str, context: ToolContext) -> str:
        """Delete one file from your instance code."""
        return await asyncio.to_thread(_delete_code, path, context)

    @tool(write=True)
    async def code_add_package(name: str, context: ToolContext) -> str:
        """Add a package to your instance code with `uv add`, so its scripts can import it."""
        return await asyncio.to_thread(_add_package, name, context)

    @tool(write=True)
    async def code_run(script: str, arguments: list[str], context: ToolContext) -> str:
        """Run a script from your instance code with its venv's Python, and return its output.

        It runs in the instance code's directory, with bash's timeout and output cap.
        """
        return await asyncio.to_thread(_run_code, script, arguments, context)

    return (
        code_add_package,
        code_delete,
        code_run,
        code_write,
        repository_commands_write,
        routine_list,
        routine_read,
        routine_write,
        routine_set_enabled,
        routine_delete,
        skill_write,
        skill_delete,
    )
