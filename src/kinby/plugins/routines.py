"""Load the instance's routine instructions at turn boundaries."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from cronsim import CronSim
from pydantic import BaseModel, ConfigDict, Field, Json, JsonValue  # noqa: TID251 - file boundary

from kinby.contracts import CronSchedule, PermissionMode, RoutineName, SignalAuth, Warning
from kinby.frontmatter import parse_frontmatter, required_string
from kinby.instance import Budgets, Instance
from kinby.instance.layout import ROUTINE_CODE_FILE, ROUTINE_FILE, ROUTINES_DIR
from kinby.instance.permissions import GatePolicy, constrain_mode, load_permissions
from kinby.plugins.registry import ToolRegistry, ToolSnapshot, load_tool_file
from kinby.plugins.tools import Tool


class _RawFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _RawSignal(_RawFrontmatter):
    auth: SignalAuth = SignalAuth.TOKEN
    secret: str
    signature_header: str | None = None
    delivery_header: str | None = None


class _RawRoutine(_RawFrontmatter):
    description: str
    schedule: str | None = None
    enabled: bool = True
    mode: PermissionMode | None = None
    catch_up: bool = True
    run: str | None = None
    signal: _RawSignal | None = None
    arguments: Json[dict[str, JsonValue]] = Field(default_factory=dict)
    steps: Annotated[int, Field(gt=0)] | None = None
    tokens: Annotated[int, Field(gt=0)] | None = None
    seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None


@dataclass(frozen=True)
class SharedCodeStep:
    name: str


@dataclass(frozen=True)
class TokenSignalConfig:
    secret_name: str
    delivery_header: str | None = None
    auth: Literal[SignalAuth.TOKEN] = field(init=False, default=SignalAuth.TOKEN)
    signature_header: None = field(init=False, default=None)


@dataclass(frozen=True)
class HmacSignalConfig:
    secret_name: str
    signature_header: str
    delivery_header: str | None = None
    auth: Literal[SignalAuth.HMAC_SHA256] = field(
        init=False,
        default=SignalAuth.HMAC_SHA256,
    )


SignalConfig = TokenSignalConfig | HmacSignalConfig


def strip_signal_credentials(
    signal: SignalConfig | None,
    headers: Mapping[str, str],
) -> dict[str, str]:
    excluded = {"authorization"}
    if signal is not None and signal.signature_header is not None:
        excluded.add(signal.signature_header.lower())
    return {name.lower(): value for name, value in headers.items() if name.lower() not in excluded}


def resolve_code_step(code_step: SharedCodeStep | Tool, tools: ToolSnapshot) -> Tool:
    if isinstance(code_step, Tool):
        return code_step
    resolved = tools.get(code_step.name)
    if resolved is None:
        raise ValueError(f'Code step tool "{code_step.name}" is not available.')
    return resolved


@dataclass(frozen=True)
class Routine:
    name: RoutineName
    description: str
    source: Path
    prompt: str
    schedule: CronSchedule | None
    enabled: bool
    mode: PermissionMode
    catch_up: bool
    arguments: dict[str, JsonValue]
    budgets: Budgets
    code_step: SharedCodeStep | Tool | None
    signal: SignalConfig | None = None


def load_routines(
    instance: Instance,
    *,
    policy: GatePolicy | None = None,
) -> tuple[tuple[Routine, ...], tuple[Warning, ...]]:
    policy = load_permissions(instance) if policy is None else policy
    routines: list[Routine] = []
    warnings: list[Warning] = []
    for path in _routine_paths(instance):
        try:
            routines.append(_load_routine(path, policy, instance))
        except Exception as exc:
            # Routine files are user code. One broken routine must not hide the rest.
            warnings.append(Warning(sources=(str(path),), message=str(exc)))
    return tuple(routines), tuple(warnings)


def load_routine(instance: Instance, name: RoutineName) -> Routine | None:
    path = next(
        (path for path in _routine_paths(instance) if path.parent.name == name),
        None,
    )
    if path is None:
        return None
    return _load_routine(path, load_permissions(instance), instance)


def _routine_paths(instance: Instance) -> tuple[Path, ...]:
    return tuple(sorted((instance.path / ROUTINES_DIR).glob(f"*/{ROUTINE_FILE}")))


def _signal_code_step(code_step: SharedCodeStep | Tool | None, instance: Instance) -> Tool | None:
    if code_step is None:
        return None
    if isinstance(code_step, Tool):
        return code_step
    snapshot, _ = ToolRegistry(instance.path, defaults=instance.manifest.tools.defaults).refresh()
    return resolve_code_step(code_step, snapshot)


def _load_routine(path: Path, policy: GatePolicy, instance: Instance) -> Routine:
    values, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    required_string(values, "description")
    raw = _RawRoutine.model_validate(values)
    if raw.schedule is not None:
        if len(raw.schedule.split()) != 5:
            raise ValueError("A routine schedule must have five cron fields.")
        if next(CronSim(raw.schedule, datetime.now(UTC)), None) is None:
            raise ValueError("The routine schedule has no occurrence in the next fifty years.")
    mode = raw.mode if raw.mode is not None else policy.mode
    mode = constrain_mode(mode, policy.ceiling)
    code_path = path.parent / ROUTINE_CODE_FILE
    code_step = SharedCodeStep(raw.run) if raw.run is not None else None
    if code_path.is_file():
        if raw.run is not None:
            raise ValueError("Declare either run or run.py, not both.")
        tools = load_tool_file(code_path)
        if len(tools) != 1:
            raise ValueError("run.py must define exactly one tool.")
        code_step = tools[0]
    signal = None
    if raw.signal is not None:
        if not raw.signal.secret:
            raise ValueError("Signal secret must name an environment variable.")
        if not os.environ.get(raw.signal.secret):
            raise ValueError(f"Signal secret {raw.signal.secret} is unset.")
        resolved = _signal_code_step(code_step, instance)
        if resolved is not None and "signal" not in resolved.runnable.args:
            raise ValueError("The code step has no signal parameter.")
        if raw.signal.auth is SignalAuth.HMAC_SHA256:
            if raw.signal.signature_header is None:
                raise ValueError("HMAC signal requires a signature header.")
            signal = HmacSignalConfig(
                secret_name=raw.signal.secret,
                signature_header=raw.signal.signature_header,
                delivery_header=raw.signal.delivery_header,
            )
        else:
            signal = TokenSignalConfig(
                secret_name=raw.signal.secret,
                delivery_header=raw.signal.delivery_header,
            )
    return Routine(
        name=RoutineName(path.parent.name),
        description=raw.description,
        source=path,
        prompt=body,
        schedule=CronSchedule(raw.schedule) if raw.schedule is not None else None,
        enabled=raw.enabled,
        mode=mode,
        catch_up=raw.catch_up,
        code_step=code_step,
        arguments=raw.arguments,
        budgets=Budgets(steps=raw.steps, tokens=raw.tokens, seconds=raw.seconds),
        signal=signal,
    )


def disable_routine(routine: Routine) -> None:
    """Change only the enabled field, retaining the author's other file content."""
    lines = routine.source.read_bytes().decode("utf-8").splitlines(keepends=True)
    closing = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    found = False
    for i in range(1, closing):
        key, separator, _ = lines[i].partition(":")
        if separator and key.strip() == "enabled":
            lines[i] = f"{key}: false{newline}"
            found = True
    if not found:
        lines.insert(closing, f"enabled: false{newline}")
    routine.source.write_bytes("".join(lines).encode("utf-8"))
