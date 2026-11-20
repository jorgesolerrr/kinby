"""The factory file: one YAML file that declares a factory's steps, what it requires of a
workbench, and its intake.

Its declarations are the factory file's JSON Schema, published as
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
    CodingModel,
    CommandListName,
    CronSchedule,
    FactoryName,
    HookName,
    ReasoningEffort,
    RecipeName,
    RepositoryCommands,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SetupValue,
    SignalAuth,
    SkillName,
    StepId,
    StepValue,
    SubscriptionLogin,
    ToolName,
    ValueName,
)

FACTORY_FILE = "factory.yaml"
#: Where the hub's factories folder keeps each factory version a run keeps, as
#: ``<factory>/<version>``, for the workbenches that mount it.
VERSIONS_DIR = ".versions"

_DECLARATION = ConfigDict(extra="forbid", strict=True)
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
    #: Skills of the shipped library the factory suggests a workbench picks, by name.
    suggested_picks: tuple[SkillName, ...] = ()


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class IntakeDelivery:
    """Deliveries that fire the intake, each authenticated as a routine's signal is."""

    #: The secret of the line's workbench the hub checks each delivery with.
    secret: Annotated[str, Field(min_length=1)]
    auth: SignalAuth = SignalAuth.TOKEN
    signature_header: str | None = None
    delivery_header: str | None = None


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class WorkbenchIntake:
    """What fires a line's intake routine in its owner, a schedule, deliveries or both, and the
    tool of the factory's folder that finds the work items on the line's workbench.

    The tool's ``signal`` parameter receives the delivery that fired it, or an empty mapping. Each
    mapping in the list it returns is a work item for the line.
    """

    call: ToolName
    schedule: CronSchedule | None = None
    delivery: IntakeDelivery | None = None


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
#: The branch of a run worktree a factory's worktree call names, and the base it is cut from.
BRANCH: ValueName = "branch"
BASE: ValueName = "base"


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class _Step:
    id: StepId
    #: What the step does and why, as the user reads it on the factory's page.
    description: str | None = None
    #: An off step stays in the factory, but its runs pass over it to the next step.
    off: bool = False
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


def run_value_names(step: WorkbenchStep) -> list[ValueName]:
    """The run values a command step's own commands or an approve step's summary name as
    ``{{name}}``. A list of repository commands takes none."""
    match step:
        case WorkbenchCommandStep(run=run):
            entries = [entry for entry in run if isinstance(entry, str)]
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


type WorkbenchStep = Annotated[
    WorkbenchAgentStep | WorkbenchCommandStep | WorkbenchCodeStep | WaitStep | ApproveStep,
    Field(discriminator="kind"),
]


@dataclass(frozen=True, kw_only=True, config=_DECLARATION)
class ToolCall:
    """A tool of the factory's folder the hub calls on the workbench, as ``needs_human``, ``ended``
    or ``worktree``. The tool's parameters named like a value of the run receive that value."""

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
class WorkbenchFactory:
    """A factory that runs on a workbench: what it requires of one, and its sequence of steps.

    Its tools live in its folder, as ``tools/``.
    """

    name: FactoryName
    requires: Requirements = Field(default_factory=Requirements)
    #: None leaves the line with no intake routine, so only a hand-in starts its runs.
    intake: WorkbenchIntake | None = None
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
    #: Called in the workspace before a run worktree is cut, to name its branch and the base it is
    #: cut from as ``branch`` and ``base``. None has kinby name the branch for the factory, the
    #: work item and the run, and cut it from origin's default branch.
    worktree: ToolCall | None = None


WORKBENCH_FACTORY_ADAPTER = TypeAdapter(WorkbenchFactory)


def duration_seconds(duration: Duration) -> int:
    """The number of seconds a declared duration such as ``60m`` stands for."""
    return int(duration[:-1]) * _UNIT_SECONDS[duration[-1]]


class InvalidFactoryFile(ValueError):
    """The factory file is missing, is not YAML, or does not match its schema."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


class FactoryLoader(yaml.SafeLoader):
    """Reads a factory file's booleans as YAML 1.2 does, only true and false, so a step's ``off``
    is a key."""


FactoryLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
FactoryLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile("^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def read_factory_file(folder: Path) -> WorkbenchFactory:
    """The factory file in *folder*, validated against its schema. A file written the older way
    is refused, each of its older keys with what replaced it."""
    path = folder / FACTORY_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InvalidFactoryFile((f"The factory has no {FACTORY_FILE}.",)) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidFactoryFile((f"{FACTORY_FILE} cannot be read as UTF-8 text: {exc}",)) from exc
    return parse_factory_file(text)


def parse_factory_file(text: str) -> WorkbenchFactory:
    """The factory file *text*, validated as `read_factory_file` validates a factory's file."""
    try:
        declared = yaml.load(text, Loader=FactoryLoader)
    except yaml.YAMLError as exc:
        raise InvalidFactoryFile((f"{FACTORY_FILE} is not YAML: {exc}",)) from exc
    try:
        # Validated as the JSON the published schema describes, so "1" is never an integer.
        # YAML reads a date as a date, which JSON has no type for; it is checked as text.
        as_json = json.dumps(declared, default=str)
    except ValueError as exc:
        # A YAML alias can make a structure that contains itself, which JSON cannot hold.
        raise InvalidFactoryFile((f"{FACTORY_FILE} is not a JSON document: {exc}",)) from exc
    older = _older_keys(declared)
    if older:
        raise InvalidFactoryFile(older)
    try:
        return WORKBENCH_FACTORY_ADAPTER.validate_json(as_json)
    except ValidationError as exc:
        raise InvalidFactoryFile(
            tuple(
                f"{FACTORY_FILE}: {'.'.join(map(str, error['loc'])) or 'the file'}: {error['msg']}"
                for error in exc.errors()
            )
        ) from exc


def _older_keys(declared: object) -> tuple[str, ...]:
    """Each key of a factory file written the older way that *declared* holds, with what replaced
    it."""
    if not isinstance(declared, dict):
        return ()
    problems: list[str] = []
    if "instances" in declared:
        problems.append(
            f"{FACTORY_FILE} declares instances, which no factory runs in any more. Declare what "
            "the factory needs of a workbench under requires."
        )
    steps = declared.get("steps")
    for at, step in enumerate(steps if isinstance(steps, list) else ()):
        if not isinstance(step, dict):
            continue
        named = f'Step "{step["id"]}"' if "id" in step else f"Step {at + 1}"
        problems.extend(
            f"{named} names {key}, but every step runs in the run's folder on the line's "
            "workbench. Drop it."
            for key in ("in", "branch")
            if key in step
        )
        if "hook" in step:
            problems.append(
                f"{named} names a hook. Name it as check, a hook of the factory's folder that "
                "records the step's values and names its outcome."
            )
        if step.get("kind") == "client":
            problems.append(
                f"{named} is of kind client. Make it kind agent with engine claude or codex."
            )
    return tuple(problems)
