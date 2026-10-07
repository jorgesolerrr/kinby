"""Factory runs: carry each work item through its factory's steps.

The hub queues each step for the instance it runs in. An instance takes one step at a time,
first in, first out, across every factory.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from kinby.contracts import (
    CodeStepRun,
    CommandStepRun,
    FactoryName,
    FactoryRun,
    FactoryRunCancelCommand,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunRetryCommand,
    FactoryRunSendBackCommand,
    FactoryRunStatus,
    FactoryRunSubscribeCommand,
    StepAttempt,
    StepEnding,
    StepId,
    StepResult,
    StepRunCommand,
    StepValue,
    Stream,
    ValueName,
)
from kinby.core.errors import (
    FactoryNotFound,
    FactoryRunNotFound,
    InvalidWorkItem,
    NotAnEarlierStep,
    NotAnIntake,
    RunNeedsNoHuman,
)
from kinby.factories.file import (
    AgentStep,
    ClientStep,
    CodeStep,
    CommandStep,
    FactoryFile,
    InstanceName,
    InvalidFactoryFile,
    Outcome,
    SendBack,
    Step,
    ValueType,
    duration_seconds,
)
from kinby.hub.factories import FactoryStore
from kinby.hub.registry import FactoryMember, HubRegistry

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
        run = self._existing(command.run_id)
        return FactoryRunDetail(run=run, attempts=self._registry.attempts(run.run_id))

    async def retry(self, command: FactoryRunRetryCommand) -> FactoryRun:
        """Try the step the run stopped at again, with the step's retries reset."""
        run = self._needing_human(command.run_id)
        return self._restart(run, run.step)

    async def send_back(self, command: FactoryRunSendBackCommand) -> FactoryRun:
        """Send the run back to a step before the one it stopped at, with its send-backs reset."""
        run = self._needing_human(command.run_id)
        if command.step not in _earlier(self._factory(run.factory), run.step):
            raise NotAnEarlierStep(f'Step "{command.step}" is not a step before "{run.step}".')
        return self._restart(run, command.step)

    async def cancel(self, command: FactoryRunCancelCommand) -> FactoryRun:
        """End the run at the step it stopped at. Nothing it did is undone."""
        run = self._needing_human(command.run_id)
        moved = self._registry.move_run(run.run_id, FactoryRunStatus.CANCELLED, run.step)
        self._publish(moved)
        return moved

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

    def needing_human(self, member: FactoryMember | None) -> int:
        """How many runs of the member's factory stopped for the user at a step in that instance.

        A step that runs in no instance counts toward the instance of the factory's intake.
        """
        factory = self._factory(member.factory) if member is not None else None
        if member is None or factory is None:
            return 0
        return sum(
            _instance_of(factory, run.step) == member.name
            for run in self._registry.runs(member.factory, FactoryRunStatus.NEEDS_HUMAN)
        )

    def _existing(self, run_id: UUID) -> FactoryRun:
        run = self._registry.run(run_id)
        if run is None:
            raise FactoryRunNotFound(f'Factory run "{run_id}" was not found.')
        return run

    def _needing_human(self, run_id: UUID) -> FactoryRun:
        run = self._existing(run_id)
        if run.status is not FactoryRunStatus.NEEDS_HUMAN:
            raise RunNeedsNoHuman(
                f'Factory run "{run_id}" is {run.status.value}, so it needs no human.'
            )
        return run

    def _restart(self, run: FactoryRun, step: StepId | None) -> FactoryRun:
        """Queue the run at *step* for the user, counting its retries and send-backs afresh."""
        moved = self._registry.restart_run(run.run_id, step)
        self._publish(moved)
        self._enqueue(moved)
        return moved

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

        A clean step goes where its outcome sends it, but fails when it records a value of
        another type than it declares. A failed one is tried again while its retries last, and
        leaves the run for a human after that.
        """
        run = self._registry.run(run_id)
        if run is None or run.step is None:
            raise FactoryRunNotFound(f'Factory run "{run_id}" has no step to settle.')
        factory = self._factory(run.factory)
        step = _step(factory, run.step) if factory is not None else None
        if step is not None and result.ending is StepEnding.CLEAN:
            result = _checked_types(step, result)
        if factory is None or step is None:
            self._registry.end_attempt(run_id, result)
            moved = self._registry.move_run(run_id, FactoryRunStatus.NEEDS_HUMAN, run.step)
        else:
            held = run.work_item | self._results(run_id)
            tally = _tally(factory, self._registry.counted_attempts(run_id))
            result, status, to = _settled(factory, step, result, held, tally)
            self._registry.end_attempt(run_id, result)
            moved = self._registry.move_run(run_id, status, to)
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


def _instance_of(factory: FactoryFile, step_id: StepId | None) -> InstanceName:
    """The instance the step runs in, or the intake's for a step that runs in none."""
    match _step(factory, step_id) if step_id is not None else None:
        case AgentStep() | ClientStep() | CommandStep() | CodeStep() as step:
            return step.instance
        case _:
            return factory.intake.instance


def _earlier(factory: FactoryFile | None, step_id: StepId | None) -> list[StepId]:
    """The ids of the steps before *step_id* in *factory*."""
    ids = [step.id for step in factory.steps] if factory is not None else []
    return ids[: ids.index(step_id)] if step_id in ids else []


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


def _settled(
    factory: FactoryFile,
    step: Step,
    result: StepResult,
    held: Mapping[ValueName, StepValue],
    tally: _Tally,
) -> tuple[StepResult, FactoryRunStatus, StepId | None]:
    """The result the step's attempt records, and the status and step it moves the run to.

    A clean result goes where its outcome sends it, and leaves the run for a human when it would
    send the work back along an edge more than that edge's max. One that names an outcome the
    step does not declare fails. A failed result is tried again while the step's retries last,
    and a timed-out one never is.
    """
    if result.ending is StepEnding.CLEAN:
        match _outcome(step, result.outcome):
            case None:
                result = _noted(result, f'Step "{step.id}" has no outcome "{result.outcome}".')
            case "stop":
                return result, FactoryRunStatus.CANCELLED, step.id
            case SendBack(back=back, max=most) if tally.sent_back[step.id, back] >= most:
                reason = f'Step "{step.id}" sent the work back to "{back}" {most} times, its max.'
                return _noted(result, reason, result.ending), FactoryRunStatus.NEEDS_HUMAN, step.id
            case SendBack(back=back):
                return result, FactoryRunStatus.QUEUED, back
            case "next" if step is not factory.steps[-1]:
                following = factory.steps[factory.steps.index(step) + 1]
                return result, FactoryRunStatus.QUEUED, following.id
            case "done" | "next":
                result = _checked_done(factory, held, result)
                if result.ending is StepEnding.CLEAN:
                    return result, FactoryRunStatus.DONE, None
    if result.ending is StepEnding.TIMED_OUT or tally.failures[step.id] >= _retries(step):
        return result, FactoryRunStatus.NEEDS_HUMAN, step.id
    return result, FactoryRunStatus.QUEUED, step.id


def _outcome(step: Step, name: str | None) -> Outcome | None:
    """What the outcome *name* does next, or None when the step declares no such outcome.

    A result that names no outcome takes the next step.
    """
    if name is None:
        return "next"
    return step.outcomes.get(name)


@dataclass(frozen=True)
class _Tally:
    """What a run's attempts used up of their steps' retries and send-backs."""

    #: Failed attempts, by step.
    failures: Counter[StepId]
    #: Attempts that sent the work back, by step and the step the work went back to.
    sent_back: Counter[tuple[StepId, StepId]]


def _tally(factory: FactoryFile, attempts: Iterable[StepAttempt]) -> _Tally:
    tally = _Tally(Counter(), Counter())
    for attempt in attempts:
        if attempt.ending is StepEnding.CLEAN:
            step = _step(factory, attempt.step)
            outcome = _outcome(step, attempt.outcome) if step is not None else None
            if isinstance(outcome, SendBack):
                tally.sent_back[attempt.step, outcome.back] += 1
        elif attempt.ending is not None:
            tally.failures[attempt.step] += 1
    return tally


def _checked_done(
    factory: FactoryFile, held: Mapping[ValueName, StepValue], last: StepResult
) -> StepResult:
    """The clean result that ends the run, failed when the run lacks a ``done_requires`` value.

    The values of that result count, so its hook's values are in.
    """
    values = {**held, **last.values}
    lacking = _lacking(values, factory.done_requires)
    if lacking is None:
        return last
    recorded = "no step recorded" if lacking not in values else "a step recorded as false"
    return _noted(last, f'The run\'s done_requires needs "{lacking}", which {recorded}.')


def _noted(result: StepResult, reason: str, ending: StepEnding = StepEnding.FAILED) -> StepResult:
    """*result* ending as *ending*, with *reason* after its summary."""
    summary = f"{result.summary}\n{reason}" if result.summary else reason
    return result.model_copy(update={"ending": ending, "summary": summary})


def _checked_types(step: Step, result: StepResult) -> StepResult:
    """The step's clean result, failed when a value it records is not of the type declared."""
    wrong = next(
        (
            (name, kind)
            for name, kind in step.results.items()
            if name in result.values and _value_type(result.values[name]) is not kind
        ),
        None,
    )
    if wrong is None:
        return result
    name, kind = wrong
    recorded = _value_type(result.values[name]).value
    return _noted(
        result, f'Step "{step.id}" declares "{name}" as {kind.value}, but recorded a {recorded}.'
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
