"""The factory check: validate one factory folder as a whole before the hub takes it.

It validates the factory file against its schema and each instance's setup declarations,
resolves every instance template, image recipe, routine, prompt, tool and hook the file names,
its needs_human and ended calls' among them, and checks that each step's inputs come from an
earlier step or the work item. It runs each template's tool and hook files to learn their names,
as an instance does, and initializes an instance from each template with its defaults, as a
creation does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from kinby.contracts import SetupField, SetupFieldKind, SetupFieldType, SubscriptionLogin
from kinby.factories import RECIPES_DIRECTORY
from kinby.factories.file import (
    TEMPLATES_DIR,
    AgentStep,
    ApproveStep,
    ClientStep,
    CodeStep,
    CommandStep,
    FactoryFile,
    InstanceDeclaration,
    InstanceName,
    InvalidFactoryFile,
    SendBack,
    WaitStep,
    read_factory_file,
    run_value_name,
    run_value_names,
)
from kinby.instance import (
    PLACEHOLDER_MODEL,
    InstanceExistsError,
    Tools,
    init_from_template,
    inspect_instance,
)
from kinby.instance.layout import ROUTINE_FILE, ROUTINES_DIR
from kinby.library import SHIPPED_SKILLS
from kinby.plugins.hooks import load_hooks
from kinby.plugins.registry import ToolRegistry
from kinby.setup import (
    BEHAVIOR_PROMPT_FIELD,
    BUILT_IN_FIELDS,
    readable_template_files,
    template_fields,
    value_problem,
)

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: A login's id names its Docker volume, so it takes the characters a volume name takes.
_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_PROMPT_GROUPS = ("url", "code")


@dataclass(frozen=True)
class _Template:
    """What one instance template offers the factory's steps."""

    tools: frozenset[str]
    hooks: frozenset[str]
    #: Each tool or hook file of the template that does not load.
    problems: tuple[str, ...]


def check_factory(folder: Path) -> tuple[str, ...]:
    """Every problem the factory check finds in the factory in *folder*, one message each."""
    try:
        factory = read_factory_file(folder)
    except InvalidFactoryFile as exc:
        return exc.problems
    templates = {
        name: _template(folder, folder / TEMPLATES_DIR / name)
        for name in factory.instances
        if (folder / TEMPLATES_DIR / name).is_dir()
    }
    return (
        *_name(factory, folder),
        *_instances(factory, folder, templates),
        *(problem for template in templates.values() for problem in template.problems),
        *_intake(factory, folder),
        *_steps(factory, folder, templates),
        *_run_calls(factory, templates),
        *_inputs(factory),
        *_order(factory),
        *_resumes(factory),
    )


def _template(folder: Path, template: Path) -> _Template:
    snapshot, tool_warnings = ToolRegistry(template, tools=Tools()).refresh()
    hooks, hook_warnings = load_hooks(template)
    return _Template(
        tools=frozenset(tool.name for tool in snapshot.tools),
        hooks=frozenset(hook.name for hook in hooks),
        problems=tuple(
            f"{Path(warning.sources[0]).relative_to(folder).as_posix()}: {warning.message}"
            for warning in (*tool_warnings, *hook_warnings)
            # The registry also warns about tools installed beside kinby.
            if Path(warning.sources[0]).is_relative_to(template)
        ),
    )


def _name(factory: FactoryFile, folder: Path) -> Iterator[str]:
    if factory.name != folder.name:
        yield (
            f'The factory file names the factory "{factory.name}", '
            f'but its folder is "{folder.name}".'
        )


def _instances(
    factory: FactoryFile,
    folder: Path,
    templates: dict[InstanceName, _Template],
) -> Iterator[str]:
    shipped_skills = {path.name for path in SHIPPED_SKILLS.iterdir() if path.is_dir()}
    for name, declared in factory.instances.items():
        if name not in templates:
            yield f'Instance "{name}" has no template: {TEMPLATES_DIR}/{name}/ is not a folder.'
        recipe = declared.image
        if recipe is not None and not (RECIPES_DIRECTORY / f"{recipe}.Dockerfile").is_file():
            yield f'Instance "{name}" names image recipe "{recipe}", which kinby does not ship.'
        for skill in declared.suggested_picks:
            if skill not in shipped_skills:
                yield (
                    f'Instance "{name}" suggests skill "{skill}", '
                    "which the shipped library does not hold."
                )
        declarations = list(_declarations(name, declared))
        yield from declarations
        # The defaults land only where the declarations hold, so one mistake is reported once.
        if name in templates and not declarations:
            yield from _initialization(name, declared, folder / TEMPLATES_DIR / name)


def _declarations(name: InstanceName, declared: InstanceDeclaration) -> Iterator[str]:
    for problem in (
        *_field_declaration_problems(declared.setup_fields),
        *_login_declaration_problems(declared.logins),
    ):
        yield f'Instance "{name}": {problem}'


def _field_declaration_problems(fields: Iterable[SetupField]) -> Iterator[str]:
    built_in = {field.name: field for field in BUILT_IN_FIELDS}
    seen: set[str] = set()
    for field in fields:
        if field.name in seen:
            yield f'Setup field "{field.name}" is declared more than once.'
            continue
        seen.add(field.name)
        yield from _field_failures(field)
        overridden = built_in.get(field.name)
        if field.name == BEHAVIOR_PROMPT_FIELD.name:
            yield (
                f'Setup field "{field.name}" is the behavior prompt, which starts from the '
                "template's SYSTEM.md."
            )
        elif overridden is None:
            yield from _target_failures(field)
        else:
            yield from _override_failures(field, overridden)


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


def _target_failures(field: SetupField) -> Iterator[str]:
    name = field.name
    if field.kind is SetupFieldKind.SECRET:
        if field.target is not None:
            yield f'Secret field "{name}" has a target. A secret lands in the instance secrets.'
    elif field.target is None:
        yield f'Configuration field "{name}" names no target.'


def _override_failures(field: SetupField, built_in: SetupField) -> Iterator[str]:
    if (field.kind, field.type) != (built_in.kind, built_in.type):
        yield (
            f'Setup field "{field.name}" overrides a built-in field, so it keeps its kind and type.'
        )
    if field.target is not None:
        yield (
            f'Setup field "{field.name}" overrides a built-in field, which kinby writes itself, '
            "so it takes no target."
        )


def _login_declaration_problems(logins: Iterable[SubscriptionLogin]) -> Iterator[str]:
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


def _initialization(
    name: InstanceName, declared: InstanceDeclaration, template: Path
) -> Iterator[str]:
    """An instance written from the template with its defaults, as an install writes one."""
    settings = {
        field.target.key: field.default
        for field in template_fields(declared.setup_fields)
        if field.target is not None and field.default is not None
    }
    with TemporaryDirectory(prefix="kinby-factory-check-") as temporary:
        try:
            inspect_instance(
                init_from_template(
                    Path(temporary) / "instance",
                    readable_template_files(template),
                    model=PLACEHOLDER_MODEL,
                    settings=settings,
                )
            )
        except (InstanceExistsError, ValueError, OSError) as exc:
            yield f'Instance "{name}": the template does not initialize: {exc}'


def _intake(factory: FactoryFile, folder: Path) -> Iterator[str]:
    intake = factory.intake
    if intake.instance not in factory.instances:
        yield (
            f'The intake runs in instance "{intake.instance}", which the factory does not declare.'
        )
        return
    routine = folder / TEMPLATES_DIR / intake.instance / ROUTINES_DIR / intake.routine
    if not (routine / ROUTINE_FILE).is_file():
        yield f'The intake routine "{intake.routine}" is not in instance "{intake.instance}".'


def _steps(
    factory: FactoryFile,
    folder: Path,
    templates: dict[InstanceName, _Template],
) -> Iterator[str]:
    for step in factory.steps:
        if isinstance(step, WaitStep | ApproveStep):
            continue
        if step.instance not in factory.instances:
            yield (
                f'Step "{step.id}" runs in instance "{step.instance}", '
                "which the factory does not declare."
            )
            continue
        # A missing template is reported once, not again for each step it would serve.
        template = templates.get(step.instance)
        if isinstance(step, AgentStep | ClientStep):
            prompt = (folder / step.prompt).resolve()
            if not prompt.is_relative_to(folder.resolve()) or not prompt.is_file():
                yield (
                    f'Step "{step.id}" names prompt "{step.prompt}", '
                    "which is not a file in the factory."
                )
        hook = step.hook if isinstance(step, AgentStep | ClientStep | CommandStep) else None
        if template is not None and hook is not None and hook not in template.hooks:
            yield (
                f'Step "{step.id}" names hook "{hook}", '
                f'which instance "{step.instance}" does not have.'
            )
        if isinstance(step, CodeStep) and template is not None and step.call not in template.tools:
            yield (
                f'Step "{step.id}" calls tool "{step.call}", '
                f'which instance "{step.instance}" does not have.'
            )


def _run_calls(factory: FactoryFile, templates: dict[InstanceName, _Template]) -> Iterator[str]:
    """The needs_human and ended calls each name a tool of an instance the factory declares."""
    for key, call in (("needs_human", factory.needs_human), ("ended", factory.ended)):
        if call is None:
            continue
        if call.instance not in factory.instances:
            yield f'{key} runs in instance "{call.instance}", which the factory does not declare.'
            continue
        template = templates.get(call.instance)
        if template is not None and call.call not in template.tools:
            yield f'{key} calls tool "{call.call}", which instance "{call.instance}" does not have.'


def _inputs(factory: FactoryFile) -> Iterator[str]:
    """Each value a step requires, a wait or a command names is held: by the work item or an
    earlier step. Each value done_requires or a closing filter names is held by any step."""
    held = set(factory.work_item)
    for step in factory.steps:
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


def _order(factory: FactoryFile) -> Iterator[str]:
    """Step ids are unique, work is only ever sent back to an earlier step, and a step whose
    failures are sent back declares no retry."""
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
        if step.failed is not None and step.retry is not None:
            yield (
                f'Step "{step.id}" declares both retry and failed, but a failed attempt is sent '
                "back, never tried again."
            )
        earlier.add(step.id)


def _resumes(factory: FactoryFile) -> Iterator[str]:
    """A client step only resumes the session of an earlier step of its instance and client."""
    for at, step in enumerate(factory.steps):
        if not isinstance(step, ClientStep) or step.resume is None:
            continue
        if not any(
            isinstance(earlier, ClientStep)
            and earlier.id == step.resume
            and (earlier.instance, earlier.client) == (step.instance, step.client)
            for earlier in factory.steps[:at]
        ):
            yield (
                f'Step "{step.id}" resumes "{step.resume}", which is not an earlier '
                f'{step.client} step in instance "{step.instance}".'
            )
