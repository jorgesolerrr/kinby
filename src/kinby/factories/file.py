"""The factory file: one YAML file that declares a factory's steps, and either what it requires
of a workbench or the instances its steps run in, with its intake.

These declarations are the factory file's JSON Schema, published as
``docs/schema/factory.schema.json``.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from kinby.contracts import (
    CodingClient,
    CodingModel,
    CommandListName,
    FactoryName,
    HookName,
    ReasoningEffort,
    RecipeName,
    RepositoryCommands,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SetupValue,
    SkillName,
    StepId,
    StepValue,
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
#: A whole number of seconds, minutes, hours or days, such as ``60m`` or ``7d``.
type Duration = Annotated[str, Field(pattern=r"^[1-9][0-9]*[smhd]$")]
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
#: A file in the factory's folder, by its path there.
type FactoryPath = Annotated[str, Field(min_length=1)]
#: One run value, as ``{{name}}``.
type RunValue = Annotated[str, Field(pattern=r"^\{\{[a-z_][a-z0-9_]*\}\}$")]


class ValueType(StrEnum):
    STR = "str"
    INT = "int"
    BOOL = "bool"


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class InstanceDeclaration:
    """One instance the factory's steps run in. Its template is ``instances/<name>/``."""

    #: One of the image recipes kinby ships. None builds kinby's base image.
    image: RecipeName | None = None
    #: What the instance asks for at creation, after kinby's built-in fields. A field named like
    #: a built-in one gives it another default. Each other configuration field targets a key of
    #: kinby.toml.
    setup_fields: tuple[SetupField, ...] = ()
    logins: tuple[SubscriptionLogin, ...] = ()
    #: Skills of the shipped library an instance that picks this role is offered, by name.
    suggested_picks: tuple[SkillName, ...] = ()


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class RequiredField:
    """A secret or a named value a factory requires: a setup field with no kind and no target."""

    name: str
    label: str
    description: str
    type: SetupFieldType
    required: bool
    default: SetupValue | None = None
    choices: tuple[str, ...] | None = None

    def setup_field(self, kind: SetupFieldKind) -> SetupField:
        """The setup field a workbench asks for this as, a secret or a named value."""
        return SetupField(
            name=self.name,
            label=self.label,
            description=self.description,
            kind=kind,
            type=self.type,
            required=self.required,
            default=self.default,
            choices=None if self.choices is None else list(self.choices),
        )


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class Requirements:
    """What a workbench must provide for the factory's steps to run on it."""

    #: One of the image recipes kinby ships. None runs on kinby's base image.
    image: RecipeName | None = None
    secrets: tuple[RequiredField, ...] = ()
    #: Values that are not secrets, which the workbench's record holds by name.
    values: tuple[RequiredField, ...] = ()
    #: The workbench's lists of repository commands the steps run, by name.
    commands: tuple[CommandListName, ...] = ()
    logins: tuple[SubscriptionLogin, ...] = ()
    browser: bool = False


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


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class Retry:
    """Run the step again after a failed attempt, at most ``retry`` times."""

    retry: Annotated[int, Field(ge=1)]


type Outcome = Literal["next", "done", "stop"] | SendBack
#: What a failed attempt records at a step that declares what its failures do: its summary.
FAILURE: ValueName = "failure"
#: What a wait records as true when it moves on with no signal, its quiet time passed.
QUIET: ValueName = "quiet"
#: What the user wrote when they sent the run back, which the steps from there on read.
NOTES: ValueName = "notes"


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
    #: What a failed attempt does in place of ``retry``: send the work back to an earlier step, or
    #: run this one again. Either way the next attempt reads what failed as the value ``failure``.
    failed: SendBack | Retry | None = None
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
    #: None leaves the client's default.
    model: CodingModel | None = None
    effort: ReasoningEffort | None = None
    #: The branch whose run worktree the client runs in, as the run value that names it. None
    #: runs it in the workspace.
    branch: RunValue | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class CommandStep(_Step):
    """Commands in the instance's workspace, each run without a shell. Exit code zero passes.

    A ``{{name}}`` in a command stands for the run's value of that name, as one argument. An
    entry ``{ commands: <name> }`` runs the instance's list of repository commands of that name.
    """

    kind: Literal["command"]
    instance: InstanceName = Field(alias="in")
    run: tuple[Annotated[str, Field(min_length=1)] | RepositoryCommands, ...] = Field(min_length=1)
    #: Records the values the step declares, which commands alone hand on none of.
    hook: HookName | None = None
    #: The branch whose run worktree the commands run in, as the run value that names it. None
    #: runs them in the workspace.
    branch: RunValue | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class _WorkbenchWork(_Step):
    #: A hook of the factory's folder, run however the step ended, that records the step's values
    #: and names its outcome, or fails it by raising.
    check: HookName | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WorkbenchCommandStep(_WorkbenchWork):
    """Commands in the run's folder on the workbench, each run without a shell. Exit code zero
    passes.

    A ``{{name}}`` in a command stands for the run's value of that name, as one argument. An
    entry ``{ commands: <name> }`` runs the workbench's list of repository commands of that name.
    """

    kind: Literal["command"]
    run: tuple[Annotated[str, Field(min_length=1)] | RepositoryCommands, ...] = Field(min_length=1)


class Engine(StrEnum):
    """What runs an agent step: a kinby turn, or a coding client, which keeps a session."""

    KINBY = "kinby"
    CLAUDE = "claude"
    CODEX = "codex"


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WorkbenchAgentStep(_WorkbenchWork):
    """A model doing the work from a prompt of the factory's folder, on the workbench in the run's
    folder, run by its engine."""

    kind: Literal["agent"]
    engine: Engine
    prompt: FactoryPath
    #: None leaves the coding client's default.
    model: CodingModel | None = None
    effort: ReasoningEffort | None = None
    #: An earlier agent step on the same coding client, whose session this one continues.
    resume: StepId | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class CodeStep(_Step):
    """An instance tool the model never sees.

    A mapping the tool returns is the step's values. A HookResult also names its outcome.
    """

    kind: Literal["code"]
    instance: InstanceName = Field(alias="in")
    call: ToolName


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WorkbenchCodeStep(_WorkbenchWork):
    """A tool of the factory's folder, run on the workbench. The model never sees it.

    A mapping the tool returns is the step's values. A HookResult also names its outcome.
    """

    kind: Literal["code"]
    call: ToolName


#: Where a wait's filter reads a signal: ``routine``, the routine it came in on; ``headers.<Name>``,
#: one of its headers; or ``body.<key>.<key>...``, a value of its JSON body.
type SignalField = Annotated[
    str, Field(pattern=r"^(routine|headers\.[A-Za-z0-9-]+|body(\.[^.]+)+)$")
]
type SignalFilter = Annotated[dict[SignalField, str | int | bool], Field(min_length=1)]
#: A filter value or a part of a command ``{{name}}`` stands for the run's value of that name.
_RUN_VALUE = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WaitStep(_Step):
    """Park the run until a signal matches one of the filters, or its quiet time or its deadline
    passes, whichever comes first.

    A signal matches a filter when it carries every field of the filter, each of the same type and
    value. A field the signal lacks never matches.
    """

    kind: Literal["wait"]
    signal: tuple[SignalFilter, ...] = Field(min_length=1)
    #: With no signal for this long the wait moves on by itself, with ``quiet`` recorded as true.
    quiet: Duration | None = None
    #: With no signal for this long the wait fails.
    deadline: Duration | None = None


def run_value_name(expected: str | int) -> ValueName | None:
    """The run value a filter value names as ``{{name}}``, or None for a literal value."""
    named = _RUN_VALUE.fullmatch(expected) if isinstance(expected, str) else None
    return named[1] if named is not None else None


def run_value_names(step: Step | WorkbenchStep) -> list[ValueName]:
    """The run values a command step's own commands, a client or command step's branch, or an
    approve step's summary name as ``{{name}}``. A list of repository commands takes none."""
    match step:
        case CommandStep(run=run, branch=branch):
            entries = [entry for entry in (*run, branch) if isinstance(entry, str)]
        case WorkbenchCommandStep(run=run):
            entries = [entry for entry in run if isinstance(entry, str)]
        case ClientStep(branch=str() as branch):
            entries = [branch]
        case ApproveStep(summary=summary):
            entries = [summary]
        case _:
            entries = []
    return [name for entry in entries for name in _RUN_VALUE.findall(entry)]


def with_run_values(command: str, held: Mapping[ValueName, StepValue]) -> str:
    """The command with each ``{{name}}`` it names replaced by the run's value, as one argument."""
    return _RUN_VALUE.sub(lambda named: shlex.quote(str(held[named[1]])), command)


def with_run_values_as_text(text: str, held: Mapping[ValueName, StepValue]) -> str:
    """The text with each ``{{name}}`` it names replaced by the run's value, as the user reads
    it."""
    return _RUN_VALUE.sub(lambda named: str(held[named[1]]), text)


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class ApproveStep(_Step):
    """Park the run until the user approves it or sends it back."""

    kind: Literal["approve"]
    #: What the user is asked to approve. A ``{{name}}`` in it stands for the run's value of that
    #: name.
    summary: str


type Step = Annotated[
    AgentStep | ClientStep | CommandStep | CodeStep | WaitStep | ApproveStep,
    Field(discriminator="kind"),
]


type WorkbenchStep = Annotated[
    WorkbenchAgentStep | WorkbenchCommandStep | WorkbenchCodeStep | WaitStep | ApproveStep,
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
class EndedCall:
    """An instance tool the hub calls once when a run ends done or cancelled.

    The tool's parameters named like a value of the run receive that value.
    """

    instance: InstanceName = Field(alias="in")
    call: ToolName


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class ToolCall:
    """A tool of the factory's folder the hub calls on the workbench, as ``needs_human`` or
    ``ended``. The tool's parameters named like a value of the run receive that value."""

    call: ToolName


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class Closing:
    """The signals that say a run's work finished elsewhere, which end a run that needs human.

    A signal matching one of the ``done`` filters ends it done, and one matching a ``cancelled``
    filter ends it cancelled. A filter matches as a wait's does.
    """

    done: tuple[SignalFilter, ...] = ()
    cancelled: tuple[SignalFilter, ...] = ()


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
    ended: EndedCall | None = None
    closing: Closing = Field(default_factory=Closing)


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WorkbenchFactory:
    """A factory that runs on a workbench: what it requires of one, and its sequence of steps.

    Its tools live in its folder, as ``tools/``.
    """

    name: FactoryName
    requires: Requirements = Field(default_factory=Requirements)
    #: The values each work item carries, by name.
    work_item: dict[ValueName, ValueType]
    steps: tuple[WorkbenchStep, ...] = Field(min_length=1)
    #: Values the run must hold once its last step has run.
    done_requires: tuple[ValueName, ...] = ()
    #: Called each time a run stops as needs human, with ``step`` and ``summary`` as well.
    needs_human: ToolCall | None = None
    #: Called once when a run ends done or cancelled.
    ended: ToolCall | None = None
    closing: Closing = Field(default_factory=Closing)


FACTORY_FILE_ADAPTER = TypeAdapter(FactoryFile)
WORKBENCH_FACTORY_ADAPTER = TypeAdapter(WorkbenchFactory)


def command_lists(factory: FactoryFile, role: InstanceName) -> list[CommandListName]:
    """The lists of repository commands the factory's steps in *role* name, each once."""
    return list(
        dict.fromkeys(
            entry.commands
            for step in factory.steps
            if isinstance(step, CommandStep) and step.instance == role
            for entry in step.run
            if isinstance(entry, RepositoryCommands)
        )
    )


def duration_seconds(duration: Duration) -> int:
    """The number of seconds a declared duration such as ``60m`` stands for."""
    return int(duration[:-1]) * _UNIT_SECONDS[duration[-1]]


class InvalidFactoryFile(ValueError):
    """The factory file is missing, is not YAML, or does not match its schema."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def read_factory_file(folder: Path) -> FactoryFile | WorkbenchFactory:
    """The factory file in *folder*, validated against its schema. A file that declares
    ``instances`` is a factory whose steps run in instances."""
    path = folder / FACTORY_FILE
    try:
        declared = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InvalidFactoryFile((f"The factory has no {FACTORY_FILE}.",)) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidFactoryFile((f"{FACTORY_FILE} cannot be read as UTF-8 text: {exc}",)) from exc
    except yaml.YAMLError as exc:
        raise InvalidFactoryFile((f"{FACTORY_FILE} is not YAML: {exc}",)) from exc
    try:
        # Validated as the JSON the published schema describes, so "1" is never an integer.
        # YAML reads a date as a date, which JSON has no type for; it is checked as text.
        as_json = json.dumps(declared, default=str)
    except ValueError as exc:
        # A YAML alias can make a structure that contains itself, which JSON cannot hold.
        raise InvalidFactoryFile((f"{FACTORY_FILE} is not a JSON document: {exc}",)) from exc
    instances = isinstance(declared, dict) and "instances" in declared
    adapter = FACTORY_FILE_ADAPTER if instances else WORKBENCH_FACTORY_ADAPTER
    try:
        return adapter.validate_json(as_json)
    except ValidationError as exc:
        raise InvalidFactoryFile(
            tuple(
                f"{FACTORY_FILE}: {'.'.join(map(str, error['loc'])) or 'the file'}: {error['msg']}"
                for error in exc.errors()
            )
        ) from exc
