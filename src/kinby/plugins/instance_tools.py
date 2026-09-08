"""Expose routine and skill files as gated core tools."""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from cronsim import CronSim

from kinby.instance import Instance
from kinby.instance.layout import ROUTINE_CODE_FILE, ROUTINE_FILE, ROUTINES_DIR
from kinby.instance.permissions import load_permissions
from kinby.plugins.routines import load_routine_file, load_routines
from kinby.plugins.tools import Tool, ToolContext, tool

_INSTANCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _validate_routine_name(name: str) -> None:
    if _INSTANCE_NAME.fullmatch(name) is None:
        raise ValueError(
            "A routine name must contain only letters, digits, hyphens, and underscores, "
            "and start with a letter or digit."
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
        _validate_routine_name(name)
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
    def routine_write(
        name: str,
        content: str,
        context: ToolContext,
        code: str | None = None,
    ) -> str:
        """Create or replace a routine from its complete ROUTINE.md and optional run.py."""
        _validate_routine_name(name)
        routines = context.instance.path / ROUTINES_DIR
        routines.mkdir(parents=True, exist_ok=True)
        target = routines / name
        with TemporaryDirectory(prefix=".routine-", dir=context.instance.path) as temporary:
            staged = Path(temporary) / name
            staged.mkdir()
            routine_path = staged / ROUTINE_FILE
            routine_path.write_text(content, encoding="utf-8")
            if code is not None:
                (staged / ROUTINE_CODE_FILE).write_text(code, encoding="utf-8")
            elif (target / ROUTINE_CODE_FILE).is_file():
                shutil.copy2(target / ROUTINE_CODE_FILE, staged / ROUTINE_CODE_FILE)
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
            zone = context.instance.manifest.routines.timezone
            next_firing = next(CronSim(routine.schedule, datetime.now(zone)))
            result = f"{result} Next firing: {next_firing.isoformat()}."
        if routine.signal is not None and context.instance.manifest.serve is not None:
            result = f"{result} Signal path: /signals/{name}."
        if not routine.enabled:
            result = f"{result} Status: disabled."
        return result

    return routine_list, routine_read, routine_write
