"""Factory runs: carry each work item through its factory's steps.

The hub queues each step for the instance it runs in. An instance takes one step at a time,
first in, first out, across every factory.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from kinby.contracts import (
    CodeStepRun,
    CommandStepRun,
    FactoryName,
    FactoryRun,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunStatus,
    FactoryRunSubscribeCommand,
    StepEnding,
    StepId,
    StepResult,
    StepRunCommand,
    StepValue,
    Stream,
    ValueName,
)
from kinby.core.errors import FactoryNotFound, FactoryRunNotFound, InvalidWorkItem, NotAnIntake
from kinby.factories.file import (
    AgentStep,
    ClientStep,
    CodeStep,
    CommandStep,
    FactoryFile,
    InvalidFactoryFile,
    Step,
    ValueType,
    duration_seconds,
)
from kinby.hub.factories import FactoryStore
from kinby.hub.registry import HubRegistry

#: Run one step in the instance with this id, and return its result.
type StepCaller = Callable[[UUID, StepRunCommand], Awaitable[StepResult]]

_INTERRUPTED = StepResult(
    ending=StepEnding.INTERRUPTED, summary="The hub stopped while this attempt ran."
)


@dataclass(frozen=True)
class _Refused:
    """Why a step cannot start, and the step whose attempt that fails."""

    step: StepId
    reason: str


@dataclass(frozen=True)
class _QueuedStep:
    """One step waiting for its instance, as the instance will be asked to run it."""

    run_id: UUID
    step: StepId
    command: StepRunCommand


class FactoryRuns:
    """Start runs from intake, queue their steps per instance, and record every attempt."""

    def __init__(
        self, registry: HubRegistry, factories: FactoryStore, run_step: StepCaller
    ) -> None:
        self._registry = registry
        self._factories = factories
        self._run_step = run_step
        #: The steps waiting for each instance, by instance id.
        self._queues: dict[UUID, asyncio.Queue[_QueuedStep]] = {}
        self._workers: set[asyncio.Task[None]] = set()
        self._subscribers: set[asyncio.Queue[FactoryRun]] = set()

    async def intake(self, instance_id: UUID, command: FactoryRunIntakeCommand) -> FactoryRun:
        """Start a run of the factory whose intake is this instance and routine.

        A work item that matches an unfinished run of the factory returns that run.
        """
        record = self._registry.instance(instance_id)
        member = record.factory if record is not None else None
        factory = self._factory(member.factory) if member is not None else None
        if (
            member is None
            or factory is None
            or (factory.intake.instance, factory.intake.routine) != (member.name, command.routine)
        ):
            raise NotAnIntake(
                f'Routine "{command.routine}" of this instance is no factory\'s intake.'
            )
        errors = _work_item_errors(factory.work_item, command.work_item)
        if errors:
            raise InvalidWorkItem(errors)
        run, started = self._registry.open_run(
            member.factory, command.work_item, factory.steps[0].id
        )
        if started:
            self._publish(run)
            self._enqueue(run)
        return run

    async def list(self, command: FactoryRunListCommand) -> FactoryRunListResult:
        return FactoryRunListResult(runs=self._registry.runs(command.factory, command.status))

    async def get(self, command: FactoryRunGetCommand) -> FactoryRunDetail:
        run = self._registry.run(command.run_id)
        if run is None:
            raise FactoryRunNotFound(f'Factory run "{command.run_id}" was not found.')
        return FactoryRunDetail(run=run, attempts=self._registry.attempts(run.run_id))

    async def subscribe(self, command: FactoryRunSubscribeCommand) -> Stream[FactoryRun]:
        """Each run as it changes from now on."""
        changes: asyncio.Queue[FactoryRun] = asyncio.Queue()
        self._subscribers.add(changes)

        async def items() -> AsyncGenerator[FactoryRun]:
            while True:
                yield await changes.get()

        return Stream(0, items(), _close=lambda: self._subscribers.discard(changes))

    def resume(self) -> None:
        """Pick the runs up where the previous process left them.

        An attempt it left running counts as failed, and its run follows the step's retry.
        """
        for run_id in self._registry.unfinished_attempts():
            self._settle(run_id, _INTERRUPTED)
        for run in self._registry.queued_runs():
            self._enqueue(run)

    def _enqueue(self, run: FactoryRun) -> None:
        """Queue the run's step for its instance.

        A step that cannot start fails an attempt right away: its own, or that of the step that
        should have produced a value it requires.
        """
        if run.step is None:
            raise ValueError(f'Factory run "{run.run_id}" is at no step to queue.')
        placed = self._placed(run, run.step)
        if isinstance(placed, _Refused):
            self._publish(self._registry.begin_attempt(run.run_id, placed.step))
            failed = StepResult(ending=StepEnding.FAILED, summary=placed.reason)
            moved = self._settle(run.run_id, failed)
            if moved.status is FactoryRunStatus.QUEUED:
                self._enqueue(moved)
            return
        instance_id, queued = placed
        queue = self._queues.get(instance_id)
        if queue is None:
            queue = self._queues[instance_id] = asyncio.Queue()
            worker = asyncio.create_task(self._work(instance_id, queue))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
        queue.put_nowait(queued)

    def _placed(self, run: FactoryRun, step_id: StepId) -> tuple[UUID, _QueuedStep] | _Refused:
        """The instance the run's step runs in and what it is asked, or why it cannot run."""
        factory = self._factory(run.factory)
        step = _step(factory, step_id) if factory is not None else None
        if factory is None or step is None:
            return _Refused(step_id, f'Factory "{run.factory}" has no step "{step_id}" any more.')
        results = self._results(run.run_id)
        unmet = _unmet(factory, step, run.work_item | results)
        if unmet is not None:
            return unmet
        match step:
            case CommandStep():
                timeout = duration_seconds(step.timeout) if step.timeout is not None else None
                asked = CommandStepRun(run=list(step.run), timeout_seconds=timeout)
                hook = step.hook
            case CodeStep():
                asked, hook = CodeStepRun(call=step.call), None
            case _:
                return _Refused(step.id, f'kinby does not run "{step.kind}" steps yet.')
        instance_id = next(
            (
                record.instance_id
                for record in self._registry.factory_members(run.factory)
                if record.active
                and record.factory is not None
                and record.factory.name == step.instance
            ),
            None,
        )
        if instance_id is None:
            return _Refused(
                step.id, f'Instance "{step.instance}" of factory "{run.factory}" is not installed.'
            )
        command = StepRunCommand(step=asked, hook=hook, work_item=run.work_item, results=results)
        return instance_id, _QueuedStep(run.run_id, step.id, command)

    def _results(self, run_id: UUID) -> dict[ValueName, StepValue]:
        """Every value the run's clean attempts recorded, a later one over an earlier one."""
        results: dict[ValueName, StepValue] = {}
        for attempt in self._registry.attempts(run_id):
            if attempt.ending is StepEnding.CLEAN:
                results.update(attempt.values)
        return results

    async def _work(self, instance_id: UUID, queue: asyncio.Queue[_QueuedStep]) -> None:
        """Run the instance's queued steps one at a time, in the order they were queued."""
        while True:
            queued = await queue.get()
            self._publish(self._registry.begin_attempt(queued.run_id, queued.step))
            moved = self._settle(queued.run_id, await self._run_step(instance_id, queued.command))
            if moved.status is FactoryRunStatus.QUEUED:
                self._enqueue(moved)

    def _settle(self, run_id: UUID, result: StepResult) -> FactoryRun:
        """End the run's open attempt with *result*, and move the run where the result sends it.

        A clean step moves the run to its next step, or finishes it after the last when the run
        holds what the factory's ``done_requires`` names. A failed one is tried again while its
        retries last, and leaves the run for a human after that.
        """
        run = self._registry.run(run_id)
        if run is None or run.step is None:
            raise FactoryRunNotFound(f'Factory run "{run_id}" has no step to settle.')
        factory = self._factory(run.factory)
        step = _step(factory, run.step) if factory is not None else None
        if factory is not None and step is factory.steps[-1] and result.ending is StepEnding.CLEAN:
            result = _checked_done(factory, run.work_item | self._results(run_id), result)
        self._registry.end_attempt(run_id, result)
        if factory is None or step is None:
            moved = self._registry.move_run(run_id, FactoryRunStatus.NEEDS_HUMAN, run.step)
        elif result.ending is StepEnding.CLEAN:
            following = factory.steps[factory.steps.index(step) + 1 :]
            moved = (
                self._registry.move_run(run_id, FactoryRunStatus.QUEUED, following[0].id)
                if following
                else self._registry.move_run(run_id, FactoryRunStatus.DONE, None)
            )
        else:
            failures = sum(
                attempt.step == step.id and attempt.ending is not StepEnding.CLEAN
                for attempt in self._registry.attempts(run_id)
            )
            status = (
                FactoryRunStatus.QUEUED
                if failures <= _retries(step)
                else FactoryRunStatus.NEEDS_HUMAN
            )
            moved = self._registry.move_run(run_id, status, step.id)
        self._publish(moved)
        return moved

    def _factory(self, name: FactoryName) -> FactoryFile | None:
        """The factory's file as the hub keeps it, or None once the factory is gone."""
        try:
            return self._factories.file(name)
        except FactoryNotFound, InvalidFactoryFile:
            return None

    def _publish(self, run: FactoryRun) -> None:
        for subscriber in self._subscribers:
            subscriber.put_nowait(run)


def _step(factory: FactoryFile, step_id: StepId) -> Step | None:
    return next((step for step in factory.steps if step.id == step_id), None)


def _unmet(
    factory: FactoryFile, step: Step, held: Mapping[ValueName, StepValue]
) -> _Refused | None:
    """Why *step* cannot start for lack of a value it requires, or None when the run holds them.

    The lack is charged to the step that should have produced the value: the last earlier step
    that declares it in its results. A false work item value has no such step, so the requiring
    step itself fails.
    """
    lacking = _lacking(held, step.requires)
    if lacking is None:
        return None
    earlier = factory.steps[: factory.steps.index(step)]
    producer = next((before for before in reversed(earlier) if lacking in before.results), None)
    if producer is None:
        return _Refused(
            step.id, f'Step "{step.id}" requires "{lacking}", which the work item carries as false.'
        )
    recorded = "did not record" if lacking not in held else "recorded as false"
    return _Refused(
        producer.id, f'Step "{step.id}" requires "{lacking}", which this step {recorded}.'
    )


def _checked_done(
    factory: FactoryFile, held: Mapping[ValueName, StepValue], last: StepResult
) -> StepResult:
    """The last step's clean result, failed when the run lacks a value ``done_requires`` names.

    The values of that result count, so its hook's values are in.
    """
    values = {**held, **last.values}
    lacking = _lacking(values, factory.done_requires)
    if lacking is None:
        return last
    recorded = "no step recorded" if lacking not in values else "a step recorded as false"
    reason = f'The run\'s done_requires needs "{lacking}", which {recorded}.'
    return StepResult(
        ending=StepEnding.FAILED,
        outcome=last.outcome,
        values=last.values,
        summary=f"{last.summary}\n{reason}" if last.summary else reason,
    )


def _lacking(held: Mapping[ValueName, StepValue], names: Iterable[ValueName]) -> ValueName | None:
    """The first of *names* the run does not hold, or holds as false."""
    return next((name for name in names if held.get(name, False) is False), None)


def _retries(step: Step) -> int:
    """How many times a failed step is tried again (ADR 0076).

    Once for a step a model or a coding client runs, and never for any other unless it opts in.
    """
    if step.retry is not None:
        return step.retry
    return 1 if isinstance(step, AgentStep | ClientStep) else 0


def _work_item_errors(
    declared: Mapping[ValueName, ValueType], values: Mapping[ValueName, StepValue]
) -> dict[str, str]:
    """What is wrong with each value of a work item, against the values the factory declares."""
    errors = {
        name: "The factory's work item carries no value by this name."
        for name in values
        if name not in declared
    }
    for name, kind in declared.items():
        if name not in values:
            errors[name] = "The work item needs this value."
        elif _value_type(values[name]) is not kind:
            errors[name] = f"Expected a value of type {kind.value}."
    return errors


def _value_type(value: StepValue) -> ValueType:
    # A bool is an int too, so it is matched first.
    match value:
        case bool():
            return ValueType.BOOL
        case int():
            return ValueType.INT
        case str():
            return ValueType.STR
