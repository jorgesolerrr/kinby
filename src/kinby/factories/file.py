"""The factory file: one YAML file that declares a factory's instances, intake and steps.

These declarations are the factory file's JSON Schema, published as
``docs/schema/factory.schema.json``.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from kinby.contracts import FactoryName

FACTORY_FILE = "factory.yaml"
#: Where each instance template lives in the factory's folder, as ``instances/<name>/``.
TEMPLATES_DIR = "instances"

_DECLARATION = ConfigDict(extra="forbid", strict=True)
#: A name that is also one folder or file name: an instance, its routine, an image recipe.
_FOLDER_NAME = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"

type InstanceName = Annotated[str, Field(pattern=_FOLDER_NAME)]
type RoutineName = Annotated[str, Field(pattern=_FOLDER_NAME)]
type RecipeName = Annotated[str, Field(pattern=_FOLDER_NAME)]
type StepId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
#: The name of a value a work item carries or a step result declares, such as ``branch``.
type ValueName = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*$")]
#: A whole number of seconds, minutes, hours or days, such as ``60m`` or ``7d``.
type Duration = Annotated[str, Field(pattern=r"^[1-9][0-9]*[smhd]$")]
#: A file in the factory's folder, by its path there.
type FactoryPath = Annotated[str, Field(min_length=1)]
type ToolName = Annotated[str, Field(min_length=1)]
type HookName = Annotated[str, Field(min_length=1)]


class ValueType(StrEnum):
    STR = "str"
    INT = "int"
    BOOL = "bool"


class CodingClient(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class InstanceDeclaration:
    """One instance the factory's steps run in. Its template is ``instances/<name>/``."""

    #: One of the image recipes kinby ships. None builds kinby's base image.
    image: RecipeName | None = None


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


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class CodeStep(_Step):
    """An instance tool the model never sees."""

    kind: Literal["code"]
    instance: InstanceName = Field(alias="in")
    call: ToolName


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WaitStep(_Step):
    """Park the run until a signal matches the filter, or the deadline passes."""

    kind: Literal["wait"]
    signal: dict[str, str | int]
    deadline: Duration | None = None


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


FACTORY_FILE_ADAPTER = TypeAdapter(FactoryFile)


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
        return FACTORY_FILE_ADAPTER.validate_json(json.dumps(declared, default=str))
    except ValidationError as exc:
        raise InvalidFactoryFile(
            tuple(
                f"{FACTORY_FILE}: {'.'.join(map(str, error['loc'])) or 'the file'}: {error['msg']}"
                for error in exc.errors()
            )
        ) from exc
