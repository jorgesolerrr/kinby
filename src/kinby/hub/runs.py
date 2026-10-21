"""Factory runs: carry each work item through its factory's steps.

The hub queues each step for the instance it runs in. An instance takes one step at a time,
first in, first out, across every factory. A wait or approve step parks its run, which holds no
instance until a signal, its quiet time, its deadline or the user moves it on. A client step the
subscription's limit stopped parks its run until the plan window resets.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue

from kinby.contracts import (
    AgentStepRun,
    ClientStepRun,
    CodeStepRun,
    CodingSessionId,
    CommandListName,
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
    RepositoryCommandList,
    RunStop,
    StepAttempt,
    StepAttempts,
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
    RepositoryCommandsMissing,
    RunAwaitsNoApproval,
    RunNeedsNoHuman,
)
from kinby.factories.file import (
    FAILURE,
    QUIET,
    AgentStep,
    ApproveStep,
    ClientStep,
    CodeStep,
    CommandStep,
    Duration,
    EndedCall,
    FactoryFile,
    InstanceName,
    InvalidFactoryFile,
    NeedsHumanCall,
    Outcome,
    SendBack,
    Step,
    ValueType,
    WaitStep,
    command_lists,
    duration_seconds,
    run_value_name,
    run_value_names,
    with_run_values,
)
from kinby.hub.factories import FactoryStore
from kinby.hub.models import Signal
from kinby.hub.registry import STOPS, FactoryMember, HubRegistry, ManagedInstance
from kinby.instance import ManifestError, repository_commands


@dataclass(frozen=True)
class UndeliveredCall:
    """A call the instance provably did not take, so it ran nothing (ADR 0089)."""

    reason: str


#: Run one step in the instance with this id, and return its result or why it went undelivered.
type StepCaller = Callable[[UUID, StepRunCommand], Awaitable[StepResult | UndeliveredCall]]


@dataclass(frozen=True)
class Untold:
    """Why the instance did not take a run's stop, which the hub tells it again."""

    reason: str


#: Tell the instance with this id that a run it handed in stopped. None once it took the stop.
type StopTeller = Callable[[UUID, RunStop], Awaitable[Untold | None]]

_logger = logging.getLogger(__name__)

BACKOFF_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 300.0
#: How long a client step's coding client runs when the step declares no timeout.
_CLIENT_TIMEOUT: Duration = "60m"
#: The statuses a run ends in, which the factory's ended call follows.
_ENDS = (FactoryRunStatus.DONE, FactoryRunStatus.CANCELLED)


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
    """The factory's call for a run that stopped, waiting for its instance: its needs_human call,
    or its ended call once the run ends done or cancelled.

    It is no step of the run, so it records no attempt. *status* and *generation* tie it to the
    stop that queued it, so delivering it marks only that stop reported, not a later one.
    *instance* names the role that makes the call, so a retry finds the instance that holds it by
    then.
    """

    run_id: UUID
    instance: InstanceName
    command: StepRunCommand
    status: FactoryRunStatus
    generation: int
    waited: float = 0.0

    @property
    def key(self) -> str:
        """The factory file's key that declares the call."""
        return "needs_human" if self.status is FactoryRunStatus.NEEDS_HUMAN else "ended"


@dataclass(frozen=True)
class _QueuedStop:
    """A run's stop, waiting to be told to the instance that handed the run in."""

    stop: RunStop
    waited: float = 0.0


type _Queued = _QueuedStep | _QueuedReport | _QueuedStop


class FactoryRuns:
    """Start runs from intake, queue their steps per instance, and record every attempt."""

    def __init__(
        self,
        registry: HubRegistry,
        factories: FactoryStore,
        run_step: StepCaller,
        tell_stop: StopTeller,
    ) -> None:
        self._registry = registry
        self._factories = factories
        self._run_step = run_step
        self._tell_stop = tell_stop
        #: The steps, needs_human and ended calls and run stops waiting for each instance, by
        #: instance id.
        self._queues: dict[UUID, asyncio.Queue[_Queued]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        #: The quiet time or deadline of each run parked at a wait that has one, and the reset of
        #: each run parked until its plan window resets, by run id.
        self._deadlines: dict[UUID, asyncio.Task[None]] = {}
        #: The waits a signal matched while the run was still on its way to them, by run id.
        self._woken: dict[UUID, set[StepId]] = {}
        self._subscribers: set[asyncio.Queue[FactoryRun]] = set()
        registry.attribute_runs(self._intake_instance)

    async def intake(self, instance_id: UUID, command: FactoryRunIntakeCommand) -> FactoryRun:
        """Start a run of the factory whose intake is this instance and routine.

        A work item that matches an unfinished run this instance handed in returns that run, and
        retries the step of one that needs a human. An instance lacking a list of repository
        commands its role's steps name takes no work.
        """
        record = self._registry.instance(instance_id)
        member, factory = self._membership(instance_id) or (None, None)
        if (
            record is None
            or member is None
            or factory is None
            or (factory.intake.instance, factory.intake.routine) != (member.name, command.routine)
        ):
            raise NotAnIntake(
                f'Routine "{command.routine}" of this instance is no factory\'s intake.'
            )
        errors = _work_item_errors(factory.work_item, command.work_item)
        if errors:
            raise InvalidWorkItem(errors)
        missing = _missing_commands(factory, member.name, record.path)
        if missing:
            raise RepositoryCommandsMissing(missing)
        run, started = self._registry.open_run(
            instance_id, member.factory, command.work_item, factory.steps[0].id
        )
        if started:
            self._publish(run)
            self._enqueue(run)
        elif run.status is FactoryRunStatus.NEEDS_HUMAN:
            return self._restart(run, run.step)
        return run

    async def list(self, command: FactoryRunListCommand) -> FactoryRunListResult:
        return FactoryRunListResult(
            runs=self._registry.runs(command.factory, command.status, command.instance_id)
        )

    def rework(self, run: FactoryRun) -> Rework:
        """The run's attempts per step, and its retries and send-backs by the tally rules.

        Its last attempt was not tried again or sent back yet, whatever it ended in. Without the
        factory's file, no attempt reads as either.
        """
        attempts = self._registry.attempts(run.run_id)
        factory = self._factory(run.factory)
        tally = (
            _tally(factory, attempts[:-1]) if factory is not None else _Tally(Counter(), Counter())
        )
        per_step = Counter(attempt.step for attempt in attempts)
        return Rework(
            [StepAttempts(step=step, attempts=count) for step, count in per_step.items()],
            tally.failures.total(),
            tally.sent_back.total(),
        )

    async def get(self, command: FactoryRunGetCommand) -> FactoryRunDetail:
        run = self._existing(command.run_id)
        return FactoryRunDetail(
            run=run,
            attempts=self._registry.attempts(run.run_id),
            earlier_steps=_earlier(self._factory(run.factory), run.step),
        )

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
        parks there, so a signal that lands between two steps is not lost. The instance of the
        factory's intake moves on only the runs it handed in.
        """
        membership = self._membership(instance_id)
        if membership is None:
            return
        member, factory = membership
        owner = instance_id if member.name == factory.intake.instance else None
        body = _json(signal.body)
        waits = [step for step in factory.steps if isinstance(step, WaitStep)]
        # Read before any parked run moves on, so the signal wakes each run at most once.
        working = (
            *self._registry.runs(member.factory, FactoryRunStatus.QUEUED, owner),
            *self._registry.runs(member.factory, FactoryRunStatus.RUNNING, owner),
        )
        for run in self._registry.runs(member.factory, FactoryRunStatus.PARKED, owner):
            step = _step(factory, run.step) if run.step is not None else None
            held = run.work_item | self._results(run.run_id, factory)
            if isinstance(step, WaitStep) and any(
                _matches(fields, signal, body, held) for fields in step.signal
            ):
                self._disarm(run.run_id)
                summary = f'A signal on routine "{signal.routine}" matched.'
                self._advance(run.run_id, StepResult(ending=StepEnding.CLEAN, summary=summary))
        for run in working:
            held = run.work_item | self._results(run.run_id, factory)
            for wait in waits:
                if any(_matches(fields, signal, body, held) for fields in wait.signal):
                    self._woken.setdefault(run.run_id, set()).add(wait.id)

    async def cancel(self, command: FactoryRunCancelCommand) -> FactoryRun:
        """End the run at the step it stopped or is parked at. Nothing it did is undone."""
        run = self._existing(command.run_id)
        if run.status is FactoryRunStatus.PARKED:
            self._disarm(run.run_id)
            cancelled = StepResult(ending=StepEnding.CLEAN, summary="The user cancelled the run.")
            self._registry.end_attempt(run.run_id, cancelled)
        else:
            _needing_human(run)
        return self._move(run.run_id, FactoryRunStatus.CANCELLED, run.step)

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

        An attempt it left running is no attempt (ADR 0102), and its step is offered again. A
        run parked at a wait keeps its deadline, counted from when it parked, and one parked
        until its plan window resets still wakes at the reset. A needs_human report, an ended
        call or a run's stop queued but not delivered before the hub stopped is queued again.

        The runs still unreported or uncalled and the stops still untold from before this pass
        are collected first, so a run that stops as it is queued below, which queues that fresh
        stop's own report and telling, never queues a second one for the same stop.
        """
        pending = self._registry.unreported_runs()
        uncalled = self._registry.uncalled_ends()
        untold = self._registry.untold_stops()
        for run_id in self._registry.unfinished_attempts():
            self._registry.drop_attempt(run_id)
        for run in self._registry.runs_in(FactoryRunStatus.QUEUED):
            self._enqueue(run)
        for run in self._registry.runs_in(FactoryRunStatus.PARKED):
            factory = self._factory(run.factory)
            step = (
                _step(factory, run.step) if factory is not None and run.step is not None else None
            )
            if run.wakes_at is not None:
                self._wake_at(run.run_id, run.step, run.wakes_at)
            elif isinstance(step, WaitStep):
                self._arm(run.run_id, step, self._registry.attempts(run.run_id)[-1].started_at)
        for run in pending:
            factory = self._factory(run.factory)
            if factory is not None and factory.needs_human is not None:
                attempts = self._registry.attempts(run.run_id)
                summary = attempts[-1].summary if attempts else ""
                self._report(factory, factory.needs_human, run, summary)
        for run in uncalled:
            factory = self._factory(run.factory)
            if factory is not None and factory.ended is not None:
                self._report(factory, factory.ended, run)
        for stop in untold:
            self._put_stop(_QueuedStop(stop))

    def needing_human(self, record: ManagedInstance) -> int:
        """How many runs of the instance's factory need the user at a step in that instance.

        A run needs the user when it stopped for a human or awaits approval. A step that runs in
        no instance counts toward the instance of the factory's intake. A run counts only toward
        the instance its step runs in: the intake's instance for the runs it handed in, and the
        first active instance of any other role.
        """
        member = record.factory
        factory = self._factory(member.factory) if member is not None else None
        if member is None or factory is None:
            return 0
        if member.name == factory.intake.instance:
            owner = record.instance_id
        elif self._first_in(member.factory, member.name) == record.instance_id:
            owner = None
        else:
            return 0
        return sum(
            _instance_of(factory, run.step) == member.name
            for run in (
                *self._registry.runs(member.factory, FactoryRunStatus.NEEDS_HUMAN, owner),
                *self._registry.runs(member.factory, FactoryRunStatus.PARKED, owner),
            )
            if run.status is FactoryRunStatus.NEEDS_HUMAN or _awaits_approval(factory, run)
        )

    def missing_commands(self, record: ManagedInstance) -> Sequence[CommandListName]:
        """The lists of repository commands the instance's factory role needs and it does not
        hold."""
        member = record.factory
        factory = self._factory(member.factory) if member is not None else None
        if member is None or factory is None:
            return []
        return _missing_commands(factory, member.name, record.path)

    def held_commands(self, record: ManagedInstance) -> Sequence[RepositoryCommandList]:
        """The lists of repository commands the instance holds now, when its factory role needs
        any."""
        member = record.factory
        factory = self._factory(member.factory) if member is not None else None
        if member is None or factory is None or not command_lists(factory, member.name):
            return []
        return [
            RepositoryCommandList(name=name, commands=commands)
            for name, commands in _held_commands(record.path).items()
        ]

    def _membership(self, instance_id: UUID) -> tuple[FactoryMember, FactoryFile] | None:
        """The instance's place in its factory, and that factory's file, if it has one."""
        record = self._registry.instance(instance_id)
        member = record.factory if record is not None else None
        factory = self._factory(member.factory) if member is not None else None
        if member is None or factory is None:
            return None
        return member, factory

    def _intake_instance(self, name: FactoryName) -> InstanceName | None:
        factory = self._factory(name)
        return factory.intake.instance if factory is not None else None

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

    def _queue(self, instance_id: UUID) -> asyncio.Queue[_Queued]:
        """The instance's queue, with the worker that empties it."""
        queue = self._queues.get(instance_id)
        if queue is None:
            queue = self._queues[instance_id] = asyncio.Queue()
            self._spawn(self._work(instance_id, queue))
        return queue

    def _spawn(self, work: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
                client_run = self._client_run(run, factory, step, held)
                if isinstance(client_run, _Refused):
                    return client_run
                asked, hook = client_run, step.hook
            case CommandStep():
                commands = [
                    with_run_values(entry, held) if isinstance(entry, str) else entry
                    for entry in step.run
                ]
                timeout = duration_seconds(step.timeout) if step.timeout is not None else None
                asked = CommandStepRun(
                    run=commands, timeout_seconds=timeout, branch=_branch(step, held)
                )
                hook = step.hook
            case CodeStep():
                asked, hook = CodeStepRun(call=step.call), None
            case WaitStep() | ApproveStep():
                return step
        instance_id = self._holder(run, step.instance)
        if instance_id is None:
            return _Refused(
                step.id, f'No instance holds role "{step.instance}" of factory "{run.factory}".'
            )
        command = StepRunCommand(
            step=asked,
            hook=hook,
            origin=FactoryRunOrigin(factory=run.factory, run_id=run.run_id, step=step.id),
            work_item=run.work_item,
            results=results,
        )
        return instance_id, _QueuedStep(run.run_id, step.id, command)

    def _holder(self, run: FactoryRun, instance: InstanceName) -> UUID | None:
        """The id of the instance that holds role *instance* for the run, or None when none does.

        The instance that handed the run in holds its own role for it, and no other instance of
        that role does, since one may work on another repository.
        """
        owner = self._registry.instance(run.instance_id)
        if owner is not None and owner.factory is not None and owner.factory.name == instance:
            return owner.instance_id if owner.active else None
        return self._first_in(run.factory, instance)

    def _first_in(self, factory: FactoryName, role: InstanceName) -> UUID | None:
        """The factory's first active instance of *role*, which runs the role's steps for runs
        another instance handed in."""
        return next(
            (
                record.instance_id
                for record in self._registry.factory_members(factory)
                if record.active and record.factory is not None and record.factory.name == role
            ),
            None,
        )

    def _prompt(self, run: FactoryRun, step: AgentStep | ClientStep) -> str | _Refused:
        try:
            return self._factories.prompt(run.factory, step.prompt)
        except (FactoryNotFound, OSError) as exc:
            return _Refused(step.id, f'Prompt "{step.prompt}" cannot be read: {exc}')

    def _client_run(
        self,
        run: FactoryRun,
        factory: FactoryFile,
        step: ClientStep,
        held: Mapping[ValueName, StepValue],
    ) -> ClientStepRun | _Refused:
        """What the client step asks its instance to run.

        When the step it resumes recorded no session, that step fails an attempt, as it would for
        a value it should have produced.
        """
        prompt = self._prompt(run, step)
        if isinstance(prompt, _Refused):
            return prompt
        session = None
        if step.resume is not None:
            session = self._session(run.run_id, factory, step.resume)
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
            model=step.model,
            effort=step.effort,
            branch=_branch(step, held),
        )

    def _session(self, run_id: UUID, factory: FactoryFile, step: StepId) -> CodingSessionId | None:
        """The newest coding client session recorded by any step that shares the step's session.

        Steps share a session when their resumes lead back to the same step. A step that resumes
        a lost session runs in a new one, which the steps after it continue.
        """
        resumes = {
            other.id: other.resume for other in factory.steps if isinstance(other, ClientStep)
        }

        def first(step_id: StepId) -> StepId:
            while (resumed := resumes.get(step_id)) is not None:
                step_id = resumed
            return step_id

        sharing = {step} | {other for other in resumes if first(other) == first(step)}
        return next(
            (
                attempt.session
                for attempt in reversed(self._registry.attempts(run_id))
                if attempt.step in sharing and attempt.session is not None
            ),
            None,
        )

    def _results(
        self, run_id: UUID, factory: FactoryFile, starting: StepId | None = None
    ) -> dict[ValueName, StepValue]:
        """The values the run's clean attempts, and its failures sent back, recorded on its latest
        pass through the steps.

        A later value overrides an earlier one. An attempt at a step, like *starting* one, drops
        what that step and every step after it recorded before, so a run sent back holds no value
        from the pass it left, save what the step that sent it back recorded: those values go
        with the work to the step it went back to, as if that step's pass held them. Trying a
        step again after an attempt at it that recorded nothing drops nothing, so a retry holds
        what the attempt before it held.
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
            if attempt.ending is StepEnding.CLEAN or _failed_to(factory, attempt) is not None:
                index = order.get(attempt.step, -1)
                recorded |= {name: (index, value) for name, value in attempt.values.items()}
                last, retrying = (index, attempt.values), None
        if starting is not None:
            start(starting)
        return {name: value for name, (_, value) in recorded.items()}

    async def _work(self, instance_id: UUID, queue: asyncio.Queue[_Queued]) -> None:
        """Run the instance's queued work one at a time, in the order it was queued."""
        while True:
            match await queue.get():
                case _QueuedStep() as queued:
                    await self._deliver(instance_id, queued)
                case _QueuedReport() as report:
                    await self._send_report(instance_id, report)
                case _QueuedStop() as stop:
                    await self._send_stop(instance_id, stop)

    async def _deliver(self, instance_id: UUID, queued: _QueuedStep) -> None:
        backoff = 0.0
        while True:
            self._publish(self._registry.begin_attempt(queued.run_id, queued.step))
            attempt = self._registry.attempts(queued.run_id)[-1].attempt
            command = queued.command.model_copy(update={"attempt": attempt})
            match await self._run_step(instance_id, command):
                case StepResult() as result:
                    self._advance(queued.run_id, result)
                    return
                case UndeliveredCall(reason=reason):
                    run = self._registry.drop_attempt(queued.run_id)
                    self._publish(run)
            backoff = _next_backoff(backoff)
            _logger.info(
                "Step %s of factory run %s did not reach its instance, offering it again in %s "
                "seconds: %s",
                queued.step,
                queued.run_id,
                backoff,
                reason,
            )
            await asyncio.sleep(backoff)
            record = self._registry.instance(instance_id)
            if record is None or not record.active:
                self._enqueue(run)
                return

    async def _send_report(self, instance_id: UUID, report: _QueuedReport) -> None:
        if self._stale(report):
            return
        match await self._run_step(instance_id, report.command):
            case StepResult(ending=StepEnding.CLEAN) if report.status in _ENDS:
                self._registry.mark_ended_called(report.run_id)
                return
            case StepResult(ending=StepEnding.CLEAN):
                self._registry.mark_reported(report.run_id, report.generation)
                return
            case StepResult(summary=reason) | UndeliveredCall(reason=reason):
                pass
        backoff = _next_backoff(report.waited)
        _logger.warning(
            "The %s call for factory run %s failed, trying again in %s seconds: %s",
            report.key,
            report.run_id,
            backoff,
            reason,
        )
        self._spawn(self._report_later(replace(report, waited=backoff)))

    def _stale(self, report: _QueuedReport) -> bool:
        run = self._registry.run(report.run_id)
        return (
            run is None
            or run.status is not report.status
            or self._registry.stop_generation(run.run_id) != report.generation
        )

    async def _report_later(self, report: _QueuedReport) -> None:
        await asyncio.sleep(report.waited)
        self._put_report(report)

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
        """Move the wait on once its quiet time after *parked_at* passes with no signal, or fail
        its attempt once its deadline does, whichever comes first."""
        ends: list[tuple[int, StepResult]] = []
        if step.quiet is not None:
            summary = f'No signal matched step "{step.id}" within its {step.quiet} quiet time.'
            moved_on = StepResult(ending=StepEnding.CLEAN, summary=summary, values={QUIET: True})
            ends.append((duration_seconds(step.quiet), moved_on))
        if step.deadline is not None:
            summary = f'No signal matched step "{step.id}" within its {step.deadline} deadline.'
            failed = StepResult(ending=StepEnding.FAILED, summary=summary)
            ends.append((duration_seconds(step.deadline), failed))
        if not ends:
            return
        after, result = min(ends, key=lambda end: end[0])
        left = after - (datetime.now(UTC) - parked_at).total_seconds()

        async def expire() -> None:
            await asyncio.sleep(left)
            del self._deadlines[run_id]
            self._advance(run_id, result)

        self._deadlines[run_id] = asyncio.create_task(expire())

    def _park_until(self, run_id: UUID, step: StepId, wakes_at: datetime) -> FactoryRun:
        """Park the run at *step*, holding no instance, until its plan window resets."""
        parked = self._registry.move_run(run_id, FactoryRunStatus.PARKED, step, wakes_at)
        self._publish(parked)
        self._wake_at(run_id, step, wakes_at)
        return parked

    def _wake_at(self, run_id: UUID, step: StepId | None, wakes_at: datetime) -> None:
        """Queue the run's *step* again once *wakes_at* passes."""

        async def wake() -> None:
            await asyncio.sleep((wakes_at - datetime.now(UTC)).total_seconds())
            del self._deadlines[run_id]
            self._enqueue(self._move(run_id, FactoryRunStatus.QUEUED, step))

        self._deadlines[run_id] = asyncio.create_task(wake())

    def _disarm(self, run_id: UUID) -> None:
        """Drop the quiet time, deadline or reset of a run that leaves its park."""
        armed = self._deadlines.pop(run_id, None)
        if armed is not None:
            armed.cancel()

    def _advance(self, run_id: UUID, result: StepResult) -> FactoryRun:
        """Settle the run's open attempt with *result*, and queue the step it moves the run to."""
        moved = self._settle(run_id, result)
        if moved.status is FactoryRunStatus.QUEUED:
            self._enqueue(moved)
        return moved

    def _settle(self, run_id: UUID, result: StepResult) -> FactoryRun:
        """End the run's open attempt with *result*, and move the run where the result sends it.

        A clean step goes where its outcome sends it, but fails when it records a value of
        another type than it declares. A failed one goes where the step sends its failures, or is
        tried again while its retries last, and leaves the run for a human after that. A limited
        one parks the run until its plan window resets.
        """
        run = self._registry.run(run_id)
        if run is None or run.step is None:
            raise FactoryRunNotFound(f'Factory run "{run_id}" has no step to settle.')
        if result.resets_at is not None:
            self._registry.end_attempt(run_id, result)
            return self._park_until(run_id, run.step, result.resets_at)
        factory = self._factory(run.factory)
        step = _step(factory, run.step) if factory is not None else None
        if step is not None and result.ending is StepEnding.CLEAN:
            result = _checked_types(step, result)
        if factory is None or step is None:
            self._registry.end_attempt(run_id, result)
            moved = self._move(run_id, FactoryRunStatus.NEEDS_HUMAN, run.step)
        else:
            held = run.work_item | self._results(run_id, factory)
            tally = _tally(factory, self._registry.counted_attempts(run_id))
            result, status, to = _settled(factory, step, result, held, tally)
            self._registry.end_attempt(run_id, result)
            moved = self._move(run_id, status, to)
        if (
            moved.status is FactoryRunStatus.NEEDS_HUMAN
            and factory is not None
            and factory.needs_human is not None
        ):
            self._report(factory, factory.needs_human, moved, result.summary)
        return moved

    def _move(self, run_id: UUID, status: FactoryRunStatus, step: StepId | None) -> FactoryRun:
        """Put the run in *status* at *step*, and tell the instance that handed it in when that
        stops it. A run that ends done or cancelled gets the factory's ended call."""
        factory = self._factory(self._existing(run_id).factory)
        ended = factory.ended if factory is not None and status in _ENDS else None
        moved = self._registry.move_run(run_id, status, step, ended_call=ended is not None)
        self._publish(moved)
        if moved.status in STOPS:
            stop = self._registry.run_stop(run_id, self._registry.stop_generation(run_id))
            self._put_stop(_QueuedStop(stop))
        if factory is not None and ended is not None:
            self._report(factory, ended, moved)
        return moved

    def _put_stop(self, queued: _QueuedStop) -> None:
        owner = self._registry.instance(self._existing(queued.stop.run_id).instance_id)
        if owner is None or not owner.active:
            _logger.warning(
                "Factory run %s stopped, and the instance that handed it in is gone.",
                queued.stop.run_id,
            )
            return
        self._queue(owner.instance_id).put_nowait(queued)

    async def _send_stop(self, instance_id: UUID, queued: _QueuedStop) -> None:
        stop = queued.stop
        untold = await self._tell_stop(instance_id, stop)
        if untold is None:
            self._registry.mark_told(stop.run_id, stop.generation)
            return
        backoff = _next_backoff(queued.waited)
        _logger.warning(
            "The stop of factory run %s did not reach its instance, telling it again in %s "
            "seconds: %s",
            stop.run_id,
            backoff,
            untold.reason,
        )
        self._spawn(self._tell_later(replace(queued, waited=backoff)))

    async def _tell_later(self, queued: _QueuedStop) -> None:
        await asyncio.sleep(queued.waited)
        self._put_stop(queued)

    def _report(
        self,
        factory: FactoryFile,
        call: NeedsHumanCall | EndedCall,
        run: FactoryRun,
        summary: str | None = None,
    ) -> None:
        """Queue the factory's call for the run as it stopped: the needs_human call, with the
        *summary* it stopped with, or the ended call.

        A run that ended done is at no step, so the call names the step of its last attempt.
        """
        step = run.step if run.step is not None else self._registry.attempts(run.run_id)[-1].step
        command = StepRunCommand(
            step=CodeStepRun(call=call.call, summary=summary),
            origin=FactoryRunOrigin(factory=run.factory, run_id=run.run_id, step=step),
            work_item=run.work_item,
            results=self._results(run.run_id, factory),
        )
        generation = self._registry.stop_generation(run.run_id)
        self._put_report(_QueuedReport(run.run_id, call.instance, command, run.status, generation))

    def _put_report(self, report: _QueuedReport) -> None:
        factory = report.command.origin.factory
        instance_id = self._holder(self._existing(report.run_id), report.instance)
        if instance_id is None:
            _logger.warning(
                "Factory run %s stopped, and no instance holds role %s of factory %s to make its "
                "%s call.",
                report.run_id,
                report.instance,
                factory,
                report.key,
            )
            return
        self._queue(instance_id).put_nowait(report)

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


def _branch(step: ClientStep | CommandStep, held: Mapping[ValueName, StepValue]) -> str | None:
    """The run's value of the branch the step names, or None when it names none."""
    name = None if step.branch is None else run_value_name(step.branch)
    return None if name is None else str(held[name])


def _unmet(
    factory: FactoryFile, step: Step, held: Mapping[ValueName, StepValue]
) -> _Refused | None:
    """Why *step* cannot start for lack of a value it requires, or None when the run holds them.

    A command step also requires each value its commands name, and a client step its branch. The
    lack is charged to the step that should have produced the value: the last earlier step that
    declares it in its results. A false work item value has no such step, so the requiring step
    itself fails.
    """
    named = run_value_names(step)
    lacking = _lacking(held, (*step.requires, *named))
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
    step does not declare fails. A failed result at a step that declares where its failures go
    records what failed as ``failure`` and goes there, up to that edge's max. Any other failed
    result is tried again while the step's retries last. A timed-out one, or one that needs a
    human, never is.
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
    if result.ending is StepEnding.FAILED and step.failed is not None:
        result = result.model_copy(update={"values": {**result.values, FAILURE: result.summary}})
        back, most = step.failed.back, step.failed.max
        if tally.sent_back[step.id, back] >= most:
            reason = f'Step "{step.id}" sent its failures back to "{back}" {most} times, its max.'
            return _noted(result, reason), FactoryRunStatus.NEEDS_HUMAN, step.id
        return result, FactoryRunStatus.QUEUED, back
    never_again = result.ending in (StepEnding.TIMED_OUT, StepEnding.NEEDS_HUMAN)
    if never_again or tally.failures[step.id] >= _retries(step):
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
class Rework:
    """What a run did again: its attempts per step, in the order it first reached each."""

    attempts: list[StepAttempts]
    retries: int
    send_backs: int


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
        elif (failed_to := _failed_to(factory, attempt)) is not None:
            tally.sent_back[attempt.step, failed_to.back] += 1
        elif attempt.ending not in (None, StepEnding.LIMITED):
            tally.failures[attempt.step] += 1
    return tally


def _failed_to(factory: FactoryFile, attempt: StepAttempt) -> SendBack | None:
    """Where a failed attempt sends the work, when its step declares where its failures go."""
    step = _step(factory, attempt.step)
    return step.failed if step is not None and attempt.ending is StepEnding.FAILED else None


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


def _next_backoff(waited: float) -> float:
    return min(max(waited * 2, BACKOFF_SECONDS), BACKOFF_CAP_SECONDS)


def _missing_commands(
    factory: FactoryFile, role: InstanceName, instance: Path
) -> list[CommandListName]:
    """The lists of repository commands the factory's steps in *role* name that the instance in
    *instance* does not hold now."""
    needed = command_lists(factory, role)
    held = _held_commands(instance) if needed else {}
    return [name for name in needed if name not in held]


def _held_commands(instance: Path) -> dict[CommandListName, list[str]]:
    """The lists of repository commands the instance in *instance* holds now. One whose manifest
    cannot be read holds none."""
    try:
        return repository_commands(instance)
    except ManifestError:
        return {}


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
