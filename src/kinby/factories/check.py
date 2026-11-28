"""The factory check: validate one factory folder as a whole before the hub takes it.

It validates the factory file against its schema and what it requires of a workbench, resolves
every image recipe, prompt, tool and check the file names, its run calls' among them, and checks
that each step's inputs come from an earlier step, the work item or the workbench's named values.
It treats an off step as missing for the steps that are on.
It runs the folder's tool and hook files to learn their names, as a workbench does, and holds the
steps to what the factory requires of the workbench.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kinby.contracts import (
    RepositoryCommands,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SignalAuth,
    StepId,
    SubscriptionLogin,
    ValueName,
)
from kinby.factories import ships_recipe
from kinby.factories.file import (
    BASE,
    BRANCH,
    FAILURE,
    NOTES,
    QUIET,
    ApproveStep,
    Engine,
    InvalidFactoryFile,
    Retry,
    SendBack,
    WaitStep,
    WorkbenchAgentStep,
    WorkbenchCodeStep,
    WorkbenchCommandStep,
    WorkbenchFactory,
    read_factory_file,
    run_value_name,
    run_value_names,
)
from kinby.instance import Tools
from kinby.library import SHIPPED_SKILLS
from kinby.plugins.hooks import load_hooks
from kinby.plugins.registry import ToolRegistry
from kinby.setup import value_problem

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: A login's id names its Docker volume, so it takes the characters a volume name takes.
_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_PROMPT_GROUPS = ("url", "code")


@dataclass(frozen=True)
class _Folder:
    """What the factory's folder offers its steps."""

    #: Its tools, without those installed beside kinby, which never reach a workbench.
    tools: frozenset[str]
    hooks: frozenset[str]
    #: Each tool or hook file of the folder that does not load.
    problems: tuple[str, ...]


def check_factory(folder: Path) -> tuple[str, ...]:
    """Every problem the factory check finds in the factory in *folder*, one message each."""
    try:
        factory = read_factory_file(folder)
    except InvalidFactoryFile as exc:
        return exc.problems
    own = _folder(folder)
    return (
        *_name(factory, folder),
        *_requirements(factory),
        *own.problems,
        *_steps(factory, folder, own),
        *_inputs(factory),
        *_clashes(factory),
        *_order(factory),
        *_resumes(factory),
        *(f'Step "{step}" {problem}' for step, problem in off_dependents(factory)),
        *_all_off(factory),
    )


def off_dependents(factory: WorkbenchFactory) -> Iterator[tuple[StepId, str]]:
    """Each step that's on and needs a step that's off, with what it needs: a session it resumes,
    a step it sends work back to, or a value only an off step records."""
    off = {step.id for step in factory.steps if step.off}
    held = set(factory.work_item) | {value.name for value in factory.requires.values}
    if factory.worktree is not None:
        held |= {BRANCH, BASE}
    on_results = (
        held
        | {FAILURE, NOTES, QUIET}
        | {name for step in factory.steps if not step.off for name in step.results}
    )
    all_off_results = {name: step.id for step in factory.steps if step.off for name in step.results}
    recorded_off: dict[ValueName, StepId] = {}
    for step in factory.steps:
        if step.off:
            recorded_off |= dict.fromkeys(step.results, step.id)
            continue
        if isinstance(step, WorkbenchAgentStep) and step.resume in off:
            yield step.id, f'resumes "{step.resume}", which is off.'
        for outcome in (*step.outcomes.values(), step.failed):
            if isinstance(outcome, SendBack) and outcome.back in off:
                yield step.id, f'sends work back to "{outcome.back}", which is off.'
        if step.when is not None and step.when not in on_results and step.when in all_off_results:
            yield (
                step.id,
                f'runs when "{step.when}", which only off step '
                f'"{all_off_results[step.when]}" records.',
            )
        filters = step.signal if isinstance(step, WaitStep) else ()
        taken = (
            *step.requires,
            *(run_value_name(expected) for fields in filters for expected in fields.values()),
            *run_value_names(step),
        )
        for value in dict.fromkeys(taken):
            if value is not None and value not in held and value in recorded_off:
                yield (
                    step.id,
                    f'takes "{value}", which only off step "{recorded_off[value]}" records.',
                )
        held.update(step.results)


def _all_off(factory: WorkbenchFactory) -> Iterator[str]:
    if all(step.off for step in factory.steps):
        yield "Every step is off, so a run has no step to take."


def _folder(folder: Path) -> _Folder:
    snapshot, tool_warnings = ToolRegistry(folder, tools=Tools()).refresh()
    hooks, hook_warnings = load_hooks(folder)
    return _Folder(
        tools=frozenset(
            tool.name for tool in snapshot.tools if tool.source.is_relative_to(folder.resolve())
        ),
        hooks=frozenset(hook.name for hook in hooks),
        problems=tuple(
            f"{Path(warning.sources[0]).relative_to(folder).as_posix()}: {warning.message}"
            for warning in (*tool_warnings, *hook_warnings)
            # The registry also warns about tools installed beside kinby.
            if Path(warning.sources[0]).is_relative_to(folder)
        ),
    )


def _name(factory: WorkbenchFactory, folder: Path) -> Iterator[str]:
    if factory.name != folder.name:
        yield (
            f'The factory file names the factory "{factory.name}", '
            f'but its folder is "{folder.name}".'
        )


def _unshipped(skills: Iterable[str]) -> list[str]:
    """Those of *skills* the shipped library does not hold."""
    shipped = {path.name for path in SHIPPED_SKILLS.iterdir() if path.is_dir()}
    return [skill for skill in skills if skill not in shipped]


def _requirements(factory: WorkbenchFactory) -> Iterator[str]:
    required = factory.requires
    if required.image is not None and not ships_recipe(required.image):
        yield f'The factory requires image recipe "{required.image}", which kinby does not ship.'
    fields = [
        *(field.setup_field(SetupFieldKind.SECRET) for field in required.secrets),
        *(field.setup_field(SetupFieldKind.CONFIG) for field in required.values),
    ]
    seen: set[str] = set()
    for field in fields:
        if field.name in seen:
            yield f'Setup field "{field.name}" is declared more than once.'
        else:
            seen.add(field.name)
            yield from _field_failures(field)
    yield from login_declaration_problems(required.logins)
    for skill in _unshipped(required.suggested_picks):
        yield f'The factory suggests skill "{skill}", which the shipped library does not hold.'


def _steps(factory: WorkbenchFactory, folder: Path, own: _Folder) -> Iterator[str]:
    for step in factory.steps:
        if isinstance(step, WorkbenchAgentStep):
            yield from _prompt(folder, step)
        if isinstance(step, WorkbenchCommandStep):
            for entry in step.run:
                if isinstance(entry, RepositoryCommands) and (
                    entry.commands not in factory.requires.commands
                ):
                    yield (
                        f'Step "{step.id}" runs command list "{entry.commands}", '
                        "which requires does not declare."
                    )
        if isinstance(step, WorkbenchCodeStep) and step.call not in own.tools:
            yield (
                f'Step "{step.id}" calls tool "{step.call}", '
                "which the factory's folder does not hold."
            )
        check = step.check if not isinstance(step, WaitStep | ApproveStep) else None
        if check is not None and check not in own.hooks:
            yield (
                f'Step "{step.id}" names check "{check}", '
                "which the factory's folder does not hold."
            )
    intake = factory.intake
    if intake is not None and intake.schedule is None and intake.delivery is None:
        yield "The intake declares no trigger: give it a schedule, a delivery or both."
    delivery = None if intake is None else intake.delivery
    if delivery is not None:
        if _ENVIRONMENT_NAME.fullmatch(delivery.secret) is None:
            yield f'The delivery secret "{delivery.secret}" is not an environment variable name.'
        if delivery.auth is SignalAuth.HMAC_SHA256 and delivery.signature_header is None:
            yield "An hmac-sha256 delivery names its signature_header."
    for key, call in (
        ("intake", intake),
        ("needs_human", factory.needs_human),
        ("ended", factory.ended),
        ("worktree", factory.worktree),
    ):
        if call is not None and call.call not in own.tools:
            yield f'{key} calls tool "{call.call}", which the factory\'s folder does not hold.'


def _field_failures(field: SetupField) -> Iterator[str]:
    name = field.name
    secret = field.kind is SetupFieldKind.SECRET
    if secret and _ENVIRONMENT_NAME.fullmatch(name) is None:
        yield f'Secret field "{name}" is not an environment variable name.'
    if not field.label.strip():
        yield f'Setup field "{name}" has no label.'
    if not field.description.strip():
        yield f'Setup field "{name}" has no description.'
    if field.choices is not None and field.type is not SetupFieldType.CHOICE:
        yield f'Setup field "{name}" has choices, but it is not a choice field.'
    if field.type is SetupFieldType.CHOICE and not field.choices:
        yield f'Choice field "{name}" offers no choices.'
    if secret and field.type not in (SetupFieldType.TEXT, SetupFieldType.MULTILINE):
        yield f'Secret field "{name}" is {field.type}. A secret is text or multiline.'
    if field.default is None:
        return
    if secret:
        yield f'Secret field "{name}" has a default. A secret never ships in a template.'
    elif (problem := value_problem(field, field.default)) is not None:
        yield f'Setup field "{name}" has a default that {problem}.'


def login_declaration_problems(logins: Iterable[SubscriptionLogin]) -> Iterator[str]:
    seen: set[str] = set()
    for login in logins:
        if login.id in seen:
            yield f'Login "{login.id}" is declared more than once.'
            continue
        seen.add(login.id)
        try:
            groups = re.compile(login.prompt_pattern).groupindex
        except re.error as exc:
            yield f'Login "{login.id}" has a prompt pattern that does not compile: {exc}.'
        else:
            for group in _PROMPT_GROUPS:
                if group not in groups:
                    yield (
                        f'Login "{login.id}" has a prompt pattern without the named group '
                        f'"{group}".'
                    )
        if _VOLUME_NAME.fullmatch(login.id) is None:
            yield (
                f'Login "{login.id}" has an id that cannot name a volume: use letters, digits, '
                '".", "_" and "-", starting with a letter or digit.'
            )
        if not PurePosixPath(login.volume).is_absolute():
            yield (
                f'Login "{login.id}" mounts its volume at "{login.volume}", '
                "which is not an absolute path."
            )


def _prompt(folder: Path, step: WorkbenchAgentStep) -> Iterator[str]:
    prompt = (folder / step.prompt).resolve()
    if not prompt.is_relative_to(folder.resolve()) or not prompt.is_file():
        yield f'Step "{step.id}" names prompt "{step.prompt}", which is not a file in the factory.'


def _inputs(factory: WorkbenchFactory) -> Iterator[str]:
    """Each value a step requires, a wait or a command names is held: by the work item, the
    workbench's named values, the factory's worktree call or an earlier step. Each value a step's
    when, done_requires or a closing filter names is held by any step."""
    held = set(factory.work_item) | {value.name for value in factory.requires.values}
    if factory.worktree is not None:
        held |= {BRANCH, BASE}
    declared = (
        held | {FAILURE, NOTES, QUIET} | {name for step in factory.steps for name in step.results}
    )
    for step in factory.steps:
        if step.when is not None and step.when not in declared:
            yield (
                f'Step "{step.id}" runs when "{step.when}", which no step declares in its '
                "results and the work item does not carry."
            )
        for value in step.requires:
            if value not in held:
                yield (
                    f'Step "{step.id}" requires "{value}", which no earlier step declares in its '
                    "results and the work item does not carry."
                )
        filters = step.signal if isinstance(step, WaitStep) else ()
        for field, expected in (item for fields in filters for item in fields.items()):
            value = run_value_name(expected)
            if value is not None and value not in held:
                yield (
                    f'Step "{step.id}" matches "{field}" against "{value}", which no earlier '
                    "step declares in its results and the work item does not carry."
                )
        for value in run_value_names(step):
            if value not in held:
                yield (
                    f'Step "{step.id}" names "{value}", which no earlier step '
                    "declares in its results and the work item does not carry."
                )
        held.update(step.results)
    for value in factory.done_requires:
        if value not in held:
            yield (
                f'done_requires names "{value}", which no step declares in its results '
                "and the work item does not carry."
            )
    closing = (*factory.closing.done, *factory.closing.cancelled)
    for field, expected in (item for fields in closing for item in fields.items()):
        value = run_value_name(expected)
        if value is not None and value not in held:
            yield (
                f'closing matches "{field}" against "{value}", which no step declares in its '
                "results and the work item does not carry."
            )


def _clashes(factory: WorkbenchFactory) -> Iterator[str]:
    """No named value shares its name with another value a run holds."""
    for value in factory.requires.values:
        name = value.name
        if name in factory.work_item:
            yield f'Named value "{name}" shares its name with a work item value.'
        if name in (BRANCH, BASE, NOTES):
            yield f'Named value "{name}" shares its name with a value kinby keeps for a run.'
        for step in factory.steps:
            if name in step.results:
                yield f'Named value "{name}" shares its name with a result of step "{step.id}".'


def _order(factory: WorkbenchFactory) -> Iterator[str]:
    """Step ids are unique, work is only ever sent back to an earlier step, and a step that
    declares what its failures do declares no retry."""
    earlier: set[str] = set()
    for step in factory.steps:
        if step.id in earlier:
            yield f'Step id "{step.id}" is used by more than one step.'
        for outcome in (*step.outcomes.values(), step.failed):
            if isinstance(outcome, SendBack) and outcome.back not in earlier:
                yield (
                    f'Step "{step.id}" sends work back to "{outcome.back}", '
                    "which is not an earlier step."
                )
        if isinstance(step.failed, SendBack) and step.retry is not None:
            yield (
                f'Step "{step.id}" declares both retry and failed, but a failed attempt is sent '
                "back, never tried again."
            )
        if isinstance(step.failed, Retry) and step.retry is not None:
            yield (
                f'Step "{step.id}" declares both retry and failed, but a failed attempt runs '
                "again only under failed."
            )
        earlier.add(step.id)


def _resumes(factory: WorkbenchFactory) -> Iterator[str]:
    """An agent step only resumes the session of an earlier agent step on its coding client, and
    the kinby engine keeps none."""
    for at, step in enumerate(factory.steps):
        if not isinstance(step, WorkbenchAgentStep) or step.resume is None:
            continue
        if step.engine is Engine.KINBY:
            yield (
                f'Step "{step.id}" resumes "{step.resume}", but the kinby engine keeps no session.'
            )
        elif not any(
            isinstance(earlier, WorkbenchAgentStep)
            and (earlier.id, earlier.engine) == (step.resume, step.engine)
            for earlier in factory.steps[:at]
        ):
            yield (
                f'Step "{step.id}" resumes "{step.resume}", which is not an earlier '
                f"{step.engine} step."
            )
