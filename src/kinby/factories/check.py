"""The factory check: validate one factory folder as a whole before the hub takes it.

It validates the factory file against its schema, resolves every instance template, image
recipe, routine, prompt, tool and hook the file names, and checks that each step's inputs come
from an earlier step or the work item. It runs each template's tool and hook files to learn
their names, as an instance does.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from kinby.contracts import SetupFieldKind, TargetFile
from kinby.factories import RECIPES_DIRECTORY
from kinby.factories.file import (
    TEMPLATES_DIR,
    AgentStep,
    ApproveStep,
    ClientStep,
    CodeStep,
    FactoryFile,
    InstanceName,
    InvalidFactoryFile,
    SendBack,
    WaitStep,
    read_factory_file,
)
from kinby.instance.layout import ROUTINE_FILE, ROUTINES_DIR
from kinby.packages import package_fields
from kinby.plugins.hooks import load_hooks
from kinby.plugins.registry import ToolRegistry


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
        *_instances(factory, templates),
        *(problem for template in templates.values() for problem in template.problems),
        *_intake(factory, folder),
        *_steps(factory, folder, templates),
        *_inputs(factory),
        *_order(factory),
    )


def _template(folder: Path, template: Path) -> _Template:
    snapshot, tool_warnings = ToolRegistry(template).refresh()
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


def _instances(factory: FactoryFile, templates: dict[InstanceName, _Template]) -> Iterator[str]:
    for name, declared in factory.instances.items():
        if name not in templates:
            yield f'Instance "{name}" has no template: {TEMPLATES_DIR}/{name}/ is not a folder.'
        recipe = declared.image
        if recipe is not None and not (RECIPES_DIRECTORY / f"{recipe}.Dockerfile").is_file():
            yield f'Instance "{name}" names image recipe "{recipe}", which kinby does not ship.'
        for field in package_fields(declared.setup_fields):
            target = field.target
            if field.kind is SetupFieldKind.CONFIG and (
                target is None or target.file is not TargetFile.KINBY_TOML
            ):
                yield (
                    f'Instance "{name}" asks for "{field.name}", which targets no key of '
                    "kinby.toml, where a template's configuration lands."
                )


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
            if template is not None and step.hook not in template.hooks:
                yield (
                    f'Step "{step.id}" names hook "{step.hook}", '
                    f'which instance "{step.instance}" does not have.'
                )
        if isinstance(step, CodeStep) and template is not None and step.call not in template.tools:
            yield (
                f'Step "{step.id}" calls tool "{step.call}", '
                f'which instance "{step.instance}" does not have.'
            )


def _inputs(factory: FactoryFile) -> Iterator[str]:
    """Each value a step requires is in the work item or an earlier step's results."""
    held = set(factory.work_item)
    for step in factory.steps:
        for value in step.requires:
            if value not in held:
                yield (
                    f'Step "{step.id}" requires "{value}", which no earlier step declares in its '
                    "results and the work item does not carry."
                )
        held.update(step.results)
    for value in factory.done_requires:
        if value not in held:
            yield (
                f'done_requires names "{value}", which no step declares in its results '
                "and the work item does not carry."
            )


def _order(factory: FactoryFile) -> Iterator[str]:
    """Step ids are unique, and work is only ever sent back to an earlier step."""
    earlier: set[str] = set()
    for step in factory.steps:
        if step.id in earlier:
            yield f'Step id "{step.id}" is used by more than one step.'
        for outcome in step.outcomes.values():
            if isinstance(outcome, SendBack) and outcome.back not in earlier:
                yield (
                    f'Step "{step.id}" sends work back to "{outcome.back}", '
                    "which is not an earlier step."
                )
        earlier.add(step.id)
