"""Factory runs: carry each work item through its factory's steps.

The hub queues each step for the instance it runs in. An instance takes one step at a time,
first in, first out, across every factory. A wait or approve step parks its run, which holds no
instance until a signal, its deadline or the user moves it on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue

from kinby.contracts import (
    AgentStepRun,
    ClientStepRun,
    CodeStepRun,
    CodingSessionId,
    CommandStepRun,
    FactoryName,
    FactoryRun,
    FactoryRunApproveCommand,
    FactoryRunCancelCommand,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunOrigin,
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
    RunAwaitsNoApproval,
    RunNeedsNoHuman,
)
from kinby.factories.file import (
    AgentStep,
    ApproveStep,
    ClientStep,
    CodeStep,
    CommandStep,
    Duration,
    FactoryFile,
    InstanceName,
    InvalidFactoryFile,
    NeedsHumanCall,
    Outcome,
    SendBack,
    Step,
    ValueType,
    WaitStep,
    duration_seconds,
    run_value_name,
    run_value_names,
    with_run_values,
)
from kinby.hub.factories import FactoryStore
from kinby.hub.models import Signal
from kinby.hub.registry import FactoryMember, HubRegistry

#: Run one step in the instance with this id, and return its result.
type StepCaller = Callable[[UUID, StepRunCommand], Awaitable[StepResult]]

_logger = logging.getLogger(__name__)

#: How long a client step's coding client runs when the step declares no timeout.
_CLIENT_TIMEOUT: Duration = "60m"
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


@dataclass(frozen=True)
class _QueuedReport:
    """The factory's needs_human call for a run that stopped, waiting for its instance.

    It is no step of the run, so it records no attempt. *generation* ties it to the stop that
    queued it, so delivering it marks only that stop reported, not a later one.
    """

    run_id: UUID
    command: StepRunCommand
    generation: int


class FactoryRuns:
    """Start runs from intake, queue their steps per instance, and record every attempt."""

    def __init__(
        self, registry: HubRegistry, factories: FactoryStore, run_step: StepCaller
    ) -> None:
        self._registry = registry
        self._factories = factories
        self._run_step = run_step
        #: The steps and needs_human calls waiting for each instance, by instance id.
        self._queues: dict[UUID, asyncio.Queue[_QueuedStep | _QueuedReport]] = {}
        self._workers: set[asyncio.Task[None]] = set()
        #: The deadline of each run parked at a wait that has one, by run id.
        self._deadlines: dict[UUID, asyncio.Task[None]] = {}
        #: The waits a signal matched while the run was still on its way to them, by run id.
        self._woken: dict[UUID, set[StepId]] = {}
        self._subscribers: set[asyncio.Queue[FactoryRun]] = set()

    async def intake(self, instance_id: UUID, command: FactoryRunIntakeCommand) -> FactoryRun:
        """Start a run of the factory whose intake is this instance and routine.

        A work item that matches an unfinished run of the factory returns that run.
        """
        member, factory = self._membership(instance_id) or (None, None)
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
        run = _needing_human(self._existing(command.run_id))
        return self._restart(run, run.step)

    async def send_back(self, command: FactoryRunSendBackCommand) -> FactoryRun:
        """Send the run back to a step before the one it stopped or awaits approval at.

        Its retries and send-backs count afresh.
        """
        run = self._existing(command.run_id)
        factory = self._factory(run.factory)
        approving = _awaits_approval(factory, run)
        if not approving:
            _needing_human(run)
        if command.step not in _earlier(factory, run.step):
            raise NotAnEarlierStep(f'Step "{command.step}" is not a step before "{run.step}".')
        if approving:
            summary = f'The user sent the run back to "{command.step}".'
            self._registry.end_attempt(
                run.run_id, StepResult(ending=StepEnding.CLEAN, summary=summary)
            )
        return self._restart(run, command.step)

    async def approve(self, command: FactoryRunApproveCommand) -> FactoryRun:
        """Move a run that awaits the user's approval on, as its approve step's next outcome."""
        run = self._existing(command.run_id)
        if not _awaits_approval(self._factory(run.factory), run):
            raise RunAwaitsNoApproval(
                f'Factory run "{run.run_id}" is {run.status.value}, so it awaits no approval.'
            )
        approved = StepResult(ending=StepEnding.CLEAN, summary="The user approved it.")
        return self._advance(run.run_id, approved)

    def signal_accepted(self, instance_id: UUID, signal: Signal) -> None:
        """Move on each run of the instance's factory whose parked wait the signal matches.

        A run still working toward a wait the signal matches moves on from it as soon as it
        parks there, so a signal that lands between two steps is not lost.
        """
        membership = self._membership(instance_id)
        if membership is None:
            return
        member, factory = membership
        body = _json(signal.body)
        waits = [step for step in factory.steps if isinstance(step, WaitStep)]
        # Read before any parked run moves on, so the signal wakes each run at most once.
        working = (
            *self._registry.runs(member.factory, FactoryRunStatus.QUEUED),
            *self._registry.runs(member.factory, FactoryRunStatus.RUNNING),
        )
        for run in self._registry.runs(member.factory, FactoryRunStatus.PARKED):
            step = _step(factory, run.step) if run.step is not None else None
            held = run.work_item | self._results(run.run_id, factory)
            if isinstance(step, WaitStep) and any(
                _matches(fields, signal, body, held) for fields in step.signal
            ):
                deadline = self._deadlines.pop(run.run_id, None)
                if deadline is not None:
                    deadline.cancel()
                summary = f'A signal on routine "{signal.routine}" matched.'
                self._advance(run.run_id, StepResult(ending=StepEnding.CLEAN, summary=summary))
        for run in working:
            held = run.work_item | self._results(run.run_id, factory)
            for wait in waits:
                if any(_matches(fields, signal, body, held) for fields in wait.signal):
                    self._woken.setdefault(run.run_id, set()).add(wait.id)

    async def cancel(self, command: FactoryRunCancelCommand) -> FactoryRun:
        """End the run at the step it stopped at. Nothing it did is undone."""
        run = _needing_human(self._existing(command.run_id))
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

        An attempt it left running counts as failed, and its run follows the step's retry. A run
        parked at a wait keeps its deadline, counted from when it parked. A needs_human report
        queued but not delivered before the hub stopped is queued again.

        The runs still unreported from before this pass are collected first, so settling an
        unfinished attempt into needs_human below, which queues that fresh stop's own report,
        never queues a second one for the same stop.
        """
        pending = self._registry.unreported_runs()
        for run_id in self._registry.unfinished_attempts():
            self._settle(run_id, _INTERRUPTED)
        for run in self._registry.runs_in(FactoryRunStatus.QUEUED):
            self._enqueue(run)
        for run in self._registry.runs_in(FactoryRunStatus.PARKED):
            factory = self._factory(run.factory)
            step = (
                _step(factory, run.step) if factory is not None and run.step is not None else None
            )
            if isinstance(step, WaitStep):
                self._arm(run.run_id, step, self._registry.attempts(run.run_id)[-1].started_at)
        for run in pending:
            factory = self._factory(run.factory)
            if factory is not None and factory.needs_human is not None:
                attempts = self._registry.attempts(run.run_id)
                summary = attempts[-1].summary if attempts else ""
                self._report(factory, factory.needs_human, run, summary)

    def needing_human(self, member: FactoryMember | None) -> int:
        """How many runs of the member's factory need the user at a step in that instance.

        A run needs the user when it stopped for a human or awaits approval. A step that runs in
        no instance counts toward the instance of the factory's intake.
        """
        factory = self._factory(member.factory) if member is not None else None
        if member is None or factory is None:
            return 0
        return sum(
            _instance_of(factory, run.step) == member.name
            for run in (
                *self._registry.runs(member.factory, FactoryRunStatus.NEEDS_HUMAN),
                *self._registry.runs(member.factory, FactoryRunStatus.PARKED),
            )
            if run.status is FactoryRunStatus.NEEDS_HUMAN or _awaits_approval(factory, run)
        )

    def _membership(self, instance_id: UUID) -> tuple[FactoryMember, FactoryFile] | None:
        """The instance's place in its factory, and that factory's file, if it has one."""
        record = self._registry.instance(instance_id)
        member = record.factory if record is not None else None
        factory = self._factory(member.factory) if member is not None else None
        if member is None or factory is None:
            return None
        return member, factory

    def _existing(self, run_id: UUID) -> FactoryRun:
        run = self._registry.run(run_id)
        if run is None:
            raise FactoryRunNotFound(f'Factory run "{run_id}" was not found.')
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
            self._advance(run.run_id, StepResult(ending=StepEnding.FAILED, summary=placed.reason))
            return
        if isinstance(placed, WaitStep | ApproveStep):
            self._park(run.run_id, placed)
            return
        instance_id, queued = placed
        self._queue(instance_id).put_nowait(queued)

    def _queue(self, instance_id: UUID) -> asyncio.Queue[_QueuedStep | _QueuedReport]:
        """The instance's queue, with the worker that empties it."""
        queue = self._queues.get(instance_id)
        if queue is None:
            queue = self._queues[instance_id] = asyncio.Queue()
            worker = asyncio.create_task(self._work(instance_id, queue))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
        return queue

    def _placed(
        self, run: FactoryRun, step_id: StepId
    ) -> tuple[UUID, _QueuedStep] | WaitStep | ApproveStep | _Refused:
        """The instance the run's step runs in and what it is asked, or why it cannot run.

        A wait or approve step runs in no instance: it is the step itself, which parks the run.
        """
        factory = self._factory(run.factory)
        step = _step(factory, step_id) if factory is not None else None
        if factory is None or step is None:
            return _Refused(step_id, f'Factory "{run.factory}" has no step "{step_id}" any more.')
        results = self._results(run.run_id, factory, step.id)
        held = run.work_item | results
        unmet = _unmet(factory, step, held)
        if unmet is not None:
            return unmet
        match step:
            case AgentStep():
                prompt = self._prompt(run, step)
                if isinstance(prompt, _Refused):
                    return prompt
                asked, hook = AgentStepRun(prompt=prompt), step.hook
            case ClientStep():
                client_run = self._client_run(run, step)
                if isinstance(client_run, _Refused):
                    return client_run
                asked, hook = client_run, step.hook
            case CommandStep():
                commands = [with_run_values(command, held) for command in step.run]
                timeout = duration_seconds(step.timeout) if step.timeout is not None else None
                asked = CommandStepRun(run=commands, timeout_seconds=timeout)
                hook = step.hook
            case CodeStep():
                asked, hook = CodeStepRun(call=step.call), None
            case WaitStep() | ApproveStep():
                return step
        instance_id = self._installed(run.factory, step.instance)
        if instance_id is None:
            return _Refused(
                step.id, f'Instance "{step.instance}" of factory "{run.factory}" is not installed.'
            )
        command = StepRunCommand(
            step=asked,
            hook=hook,
            origin=FactoryRunOrigin(factory=run.factory, run_id=run.run_id, step=step.id),
            work_item=run.work_item,
            results=results,
        )
        return instance_id, _QueuedStep(run.run_id, step.id, command)

    def _installed(self, factory: FactoryName, instance: InstanceName) -> UUID | None:
        """The id of the factory's instance by that name, or None when it is not installed."""
        return next(
            (
                record.instance_id
                for record in self._registry.factory_members(factory)
                if record.active and record.factory is not None and record.factory.name == instance
            ),
            None,
        )

    def _prompt(self, run: FactoryRun, step: AgentStep | ClientStep) -> str | _Refused:
        try:
            return self._factories.prompt(run.factory, step.prompt)
        except (FactoryNotFound, OSError) as exc:
            return _Refused(step.id, f'Prompt "{step.prompt}" cannot be read: {exc}')

    def _client_run(self, run: FactoryRun, step: ClientStep) -> ClientStepRun | _Refused:
        """What the client step asks its instance to run.

        When the step it resumes recorded no session, that step fails an attempt, as it would for
        a value it should have produced.
        """
        prompt = self._prompt(run, step)
        if isinstance(prompt, _Refused):
            return prompt
        session = None
        if step.resume is not None:
            session = self._session(run.run_id, step.resume)
            if session is None:
                return _Refused(
                    step.resume,
                    f'Step "{step.id}" resumes the session of "{step.resume}", '
                    "which this step did not record.",
                )
        return ClientStepRun(
            client=step.client,
            prompt=prompt,
            resume=session,
            timeout_seconds=duration_seconds(step.timeout or _CLIENT_TIMEOUT),
        )

    def _session(self, run_id: UUID, step: StepId) -> CodingSessionId | None:
        """The coding client session of the step's last attempt that recorded one."""
        return next(
            (
                attempt.session
                for attempt in reversed(self._registry.attempts(run_id))
                if attempt.step == step and attempt.session is not None
            ),
            None,
        )

    def _results(
        self, run_id: UUID, factory: FactoryFile, starting: StepId | None = None
    ) -> dict[ValueName, StepValue]:
        """The values the run's clean attempts recorded on its latest pass through the steps.

        A later value overrides an earlier one. An attempt at a step, like *starting* one, drops
        what that step and every step after it recorded before, so a run sent back holds no value
        from the pass it left, save what the step that sent it back recorded: those values go
        with the work to the step it went back to, as if that step's pass held them. Trying a
        step again after an attempt at it that did not end clean drops nothing, since that
        attempt recorded nothing, so a retry holds what the attempt before it held.
        """
        order = {step.id: index for index, step in enumerate(factory.steps)}
        recorded: dict[ValueName, tuple[int, StepValue]] = {}
        last: tuple[int, Mapping[ValueName, StepValue]] | None = None
        retrying: StepId | None = None

        def start(step_id: StepId) -> None:
            if step_id not in order or step_id == retrying:
                return
            at = order[step_id]
            for name, (index, _) in list(recorded.items()):
                if index >= at:
                    del recorded[name]
            if last is not None and last[0] > at:
                recorded.update((name, (at, value)) for name, value in last[1].items())

        for attempt in self._registry.attempts(run_id):
            start(attempt.step)
            last, retrying = None, attempt.step
            if attempt.ending is StepEnding.CLEAN:
                index = order.get(attempt.step, -1)
                recorded |= {name: (index, value) for name, value in attempt.values.items()}
                last, retrying = (index, attempt.values), None
        if starting is not None:
            start(starting)
        return {name: value for name, (_, value) in recorded.items()}

    async def _work(
        self, instance_id: UUID, queue: asyncio.Queue[_QueuedStep | _QueuedReport]
    ) -> None:
        """Run the instance's queued work one at a time, in the order it was queued."""
        while True:
            match await queue.get():
                case _QueuedStep(run_id=run_id, step=step, command=command):
                    self._publish(self._registry.begin_attempt(run_id, step))
                    self._advance(run_id, await self._run_step(instance_id, command))
                case _QueuedReport(run_id=run_id, command=command, generation=generation):
                    reported = await self._run_step(instance_id, command)
                    if reported.ending is StepEnding.CLEAN:
                        self._registry.mark_reported(run_id, generation)
                    else:
                        _logger.warning(
                            "The needs_human call for factory run %s failed: %s",
                            run_id,
                            reported.summary,
                        )

    def _park(self, run_id: UUID, step: WaitStep | ApproveStep) -> None:
        """Open the step's attempt with the run parked at it, holding no instance.

        A wait a signal already matched on the run's way to it moves on at once.
        """
        parked = self._registry.begin_attempt(run_id, step.id, FactoryRunStatus.PARKED)
        self._publish(parked)
        if not isinstance(step, WaitStep):
            return
        woken = self._woken.get(run_id, set())
        if step.id in woken:
            woken.discard(step.id)
            if not woken:
                del self._woken[run_id]
            summary = "A signal matched while the run was on its way to this step."
            self._advance(run_id, StepResult(ending=StepEnding.CLEAN, summary=summary))
            return
        self._arm(run_id, step, parked.updated_at)

    def _arm(self, run_id: UUID, step: WaitStep, parked_at: datetime) -> None:
        """Fail the wait's attempt once its deadline after *parked_at* passes with no signal."""
        if step.deadline is None:
            return
        deadline = step.deadline
        left = duration_seconds(deadline) - (datetime.now(UTC) - parked_at).total_seconds()

        async def expire() -> None:
            await asyncio.sleep(left)
            del self._deadlines[run_id]
            summary = f'No signal matched step "{step.id}" within its {deadline} deadline.'
            self._advance(run_id, StepResult(ending=StepEnding.FAILED, summary=summary))

        self._deadlines[run_id] = asyncio.create_task(expire())

    def _advance(self, run_id: UUID, result: StepResult) -> FactoryRun:
        """Settle the run's open attempt with *result*, and queue the step it moves the run to."""
        moved = self._settle(run_id, result)
        if moved.status is FactoryRunStatus.QUEUED:
            self._enqueue(moved)
        return moved

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
            held = run.work_item | self._results(run_id, factory)
            tally = _tally(factory, self._registry.counted_attempts(run_id))
            result, status, to = _settled(factory, step, result, held, tally)
            self._registry.end_attempt(run_id, result)
            moved = self._registry.move_run(run_id, status, to)
        self._publish(moved)
        if (
            moved.status is FactoryRunStatus.NEEDS_HUMAN
            and factory is not None
            and factory.needs_human is not None
        ):
            self._report(factory, factory.needs_human, moved, result.summary)
        return moved

    def _report(
        self, factory: FactoryFile, call: NeedsHumanCall, run: FactoryRun, summary: str
    ) -> None:
        """Queue the factory's needs_human call for the run, which stopped with this *summary*."""
        instance_id = self._installed(run.factory, call.instance)
        if instance_id is None or run.step is None:
            _logger.warning(
                "Factory run %s needs a human, and instance %s of factory %s is not installed "
                "to report it.",
                run.run_id,
                call.instance,
                run.factory,
            )
            return
        command = StepRunCommand(
            step=CodeStepRun(call=call.call, summary=summary),
            origin=FactoryRunOrigin(factory=run.factory, run_id=run.run_id, step=run.step),
            work_item=run.work_item,
            results=self._results(run.run_id, factory),
        )
        generation = self._registry.stop_generation(run.run_id)
        self._queue(instance_id).put_nowait(_QueuedReport(run.run_id, command, generation))

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


def _needing_human(run: FactoryRun) -> FactoryRun:
    if run.status is not FactoryRunStatus.NEEDS_HUMAN:
        raise RunNeedsNoHuman(
            f'Factory run "{run.run_id}" is {run.status.value}, so it needs no human.'
        )
    return run


def _awaits_approval(factory: FactoryFile | None, run: FactoryRun) -> bool:
    """Whether the run is parked at an approve step, for the user to approve or send back."""
    step = _step(factory, run.step) if factory is not None and run.step is not None else None
    return run.status is FactoryRunStatus.PARKED and isinstance(step, ApproveStep)


def _json(body: bytes) -> JsonValue:
    """The signal's body as JSON, or None when it is not JSON."""
    try:
        return json.loads(body)
    except ValueError:
        return None


def _matches(
    fields: Mapping[str, str | int],
    signal: Signal,
    body: JsonValue,
    held: Mapping[ValueName, StepValue],
) -> bool:
    """Whether the signal carries every field of a wait's filter, each of its type and value.

    A field the signal lacks, or a ``{{name}}`` the run holds no value for, never matches.
    """
    for field, expected in fields.items():
        name = run_value_name(expected)
        wanted = held.get(name) if name is not None else expected
        found = _signal_field(signal, body, field)
        if wanted is None or type(found) is not type(wanted) or found != wanted:
            return False
    return True


def _signal_field(signal: Signal, body: JsonValue, field: str) -> JsonValue:
    """The signal's value at a filter field, or None when it has none there."""
    where, _, path = field.partition(".")
    match where:
        case "routine":
            return signal.routine
        case "headers":
            return signal.headers.get(path)
    found = body
    for key in path.split("."):
        if not isinstance(found, dict):
            return None
        found = found.get(key)
    return found


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

    A command step also requires each value its commands name. The lack is charged to the step
    that should have produced the value: the last earlier step that declares it in its results. A
    false work item value has no such step, so the requiring step itself fails.
    """
    named = step.run if isinstance(step, CommandStep) else ()
    lacking = _lacking(
        held, (*step.requires, *(name for command in named for name in run_value_names(command)))
    )
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
