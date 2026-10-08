"""The factory file: one YAML file that declares a factory's instances, intake and steps.

These declarations are the factory file's JSON Schema, published as
``docs/schema/factory.schema.json``.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from kinby.contracts import (
    CodingClient,
    FactoryName,
    HookName,
    SetupField,
    StepId,
    SubscriptionLogin,
    ToolName,
    ValueName,
)

FACTORY_FILE = "factory.yaml"
#: Where each instance template lives in the factory's folder, as ``instances/<name>/``.
TEMPLATES_DIR = "instances"

_DECLARATION = ConfigDict(extra="forbid", strict=True)
#: A name that is also one folder or file name: an instance, its routine, an image recipe.
_FOLDER_NAME = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"

type InstanceName = Annotated[str, Field(pattern=_FOLDER_NAME)]
type RoutineName = Annotated[str, Field(pattern=_FOLDER_NAME)]
type RecipeName = Annotated[str, Field(pattern=_FOLDER_NAME)]
#: A whole number of seconds, minutes, hours or days, such as ``60m`` or ``7d``.
type Duration = Annotated[str, Field(pattern=r"^[1-9][0-9]*[smhd]$")]
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
#: A file in the factory's folder, by its path there.
type FactoryPath = Annotated[str, Field(min_length=1)]


class ValueType(StrEnum):
    STR = "str"
    INT = "int"
    BOOL = "bool"


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class InstanceDeclaration:
    """One instance the factory's steps run in. Its template is ``instances/<name>/``."""

    #: One of the image recipes kinby ships. None builds kinby's base image.
    image: RecipeName | None = None
    #: What the instance asks for at install, after kinby's built-in fields. A field named like
    #: a built-in one gives it another default. Each other configuration field targets a key of
    #: kinby.toml.
    setup_fields: tuple[SetupField, ...] = ()
    logins: tuple[SubscriptionLogin, ...] = ()


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class Intake:
    """The routine that hands the factory its work items, and the instance it runs in."""

    instance: InstanceName
    routine: RoutineName


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class SendBack:
    """Send the work back to an earlier step, at most ``max`` times along this edge."""

    back: StepId
    max: Annotated[int, Field(ge=1)]


type Outcome = Literal["next", "done", "stop"] | SendBack


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class _Step:
    id: StepId
    #: Values earlier steps or the work item must hold before this step starts.
    requires: tuple[ValueName, ...] = ()
    #: The values this step's result declares, by name.
    results: dict[ValueName, ValueType] = Field(default_factory=dict)
    #: What each outcome of the step's result does next.
    outcomes: dict[str, Outcome] = Field(default_factory=dict)
    retry: Annotated[int, Field(ge=0)] | None = None
    timeout: Duration | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class AgentStep(_Step):
    """A kinby turn on a new thread of the instance, with the prompt."""

    kind: Literal["agent"]
    instance: InstanceName = Field(alias="in")
    prompt: FactoryPath
    hook: HookName


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class ClientStep(_Step):
    """A coding client run directly in the instance's container, with no kinby turn around it."""

    kind: Literal["client"]
    instance: InstanceName = Field(alias="in")
    client: CodingClient
    prompt: FactoryPath
    hook: HookName
    #: An earlier client step whose session this one continues.
    resume: StepId | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class CommandStep(_Step):
    """Commands in the instance's workspace, each run without a shell. Exit code zero passes."""

    kind: Literal["command"]
    instance: InstanceName = Field(alias="in")
    run: tuple[Annotated[str, Field(min_length=1)], ...] = Field(min_length=1)
    #: Records the values the step declares, which commands alone hand on none of.
    hook: HookName | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class CodeStep(_Step):
    """An instance tool the model never sees."""

    kind: Literal["code"]
    instance: InstanceName = Field(alias="in")
    call: ToolName


#: Where a wait's filter reads a signal: ``routine``, the routine it came in on; ``headers.<Name>``,
#: one of its headers; or ``body.<key>.<key>...``, a value of its JSON body.
type SignalField = Annotated[
    str, Field(pattern=r"^(routine|headers\.[A-Za-z0-9-]+|body(\.[^.]+)+)$")
]
#: A filter value ``{{name}}`` stands for the run's value of that name.
_RUN_VALUE = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WaitStep(_Step):
    """Park the run until a signal matches the filter, or the deadline passes.

    A signal matches when it carries every field of the filter, each of the same type and value.
    A field the signal lacks never matches.
    """

    kind: Literal["wait"]
    signal: dict[SignalField, str | int] = Field(min_length=1)
    deadline: Duration | None = None


def run_value_name(expected: str | int) -> ValueName | None:
    """The run value a filter value names as ``{{name}}``, or None for a literal value."""
    named = _RUN_VALUE.fullmatch(expected) if isinstance(expected, str) else None
    return named[1] if named is not None else None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class ApproveStep(_Step):
    """Park the run until the user approves it or sends it back."""

    kind: Literal["approve"]
    #: What the user is asked to approve.
    summary: str


type Step = Annotated[
    AgentStep | ClientStep | CommandStep | CodeStep | WaitStep | ApproveStep,
    Field(discriminator="kind"),
]


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class NeedsHumanCall:
    """An instance tool the hub calls each time a run stops as needs human.

    The tool's parameters named like a value of the run receive that value, ``step`` the step
    the run stopped at, and ``summary`` the summary of that step's last attempt.
    """

    instance: InstanceName = Field(alias="in")
    call: ToolName


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class FactoryFile:
    """A factory: the instances its steps run in, its intake, and its line of steps."""

    name: FactoryName
    instances: dict[InstanceName, InstanceDeclaration] = Field(min_length=1)
    intake: Intake
    #: The values each work item carries, by name.
    work_item: dict[ValueName, ValueType]
    steps: tuple[Step, ...] = Field(min_length=1)
    #: Values the run must hold once its last step's hook has run.
    done_requires: tuple[ValueName, ...] = ()
    needs_human: NeedsHumanCall | None = None


FACTORY_FILE_ADAPTER = TypeAdapter(FactoryFile)


def duration_seconds(duration: Duration) -> int:
    """The number of seconds a declared duration such as ``60m`` stands for."""
    return int(duration[:-1]) * _UNIT_SECONDS[duration[-1]]


class InvalidFactoryFile(ValueError):
    """The factory file is missing, is not YAML, or does not match its schema."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def read_factory_file(folder: Path) -> FactoryFile:
    """The factory file in *folder*, validated against its schema."""
    path = folder / FACTORY_FILE
    try:
        declared = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InvalidFactoryFile((f"The factory has no {FACTORY_FILE}.",)) from exc
    except yaml.YAMLError as exc:
        raise InvalidFactoryFile((f"{FACTORY_FILE} is not YAML: {exc}",)) from exc
    try:
        # Validated as the JSON the published schema describes, so "1" is never an integer.
        # YAML reads a date as a date, which JSON has no type for; it is checked as text.
        as_json = json.dumps(declared, default=str)
    except ValueError as exc:
        # A YAML alias can make a structure that contains itself, which JSON cannot hold.
        raise InvalidFactoryFile((f"{FACTORY_FILE} is not a JSON document: {exc}",)) from exc
    try:
        return FACTORY_FILE_ADAPTER.validate_json(as_json)
    except ValidationError as exc:
        raise InvalidFactoryFile(
            tuple(
                f"{FACTORY_FILE}: {'.'.join(map(str, error['loc'])) or 'the file'}: {error['msg']}"
                for error in exc.errors()
            )
        ) from exc
