"""Factory runs: carry each work item through its factory's steps.

The hub queues each step for the instance or the line's workbench it runs on. Each takes one step
at a time, first in, first out, across every factory. A wait or approve step parks its run, which
holds no instance until a signal, its quiet time, its deadline or the user moves it on. A client
step the subscription's limit stopped parks its run until the plan window resets. A kinby turn the
gate asks about parks its run until the user answers, and the answer resumes the turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
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
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import JsonValue

from kinby.contracts import (
    AgentStepRun,
    AnsweredAsk,
    ApprovalDecision,
    ClientStepRun,
    CodeStepRun,
    CodingClient,
    CodingSessionId,
    CommandListName,
    CommandStepRun,
    FactoryName,
    FactoryRun,
    FactoryRunAnswerCommand,
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
    FactoryRunTakeoverFinishCommand,
    Line,
    RepositoryCommandList,
    RunFolder,
    RunFolderRemoveCommand,
    RunStop,
    StepAnswer,
    StepAttempt,
    StepAttempts,
    StepEnding,
    StepId,
    StepResult,
    StepRunCommand,
    StepValue,
    Stream,
    ValueName,
    View,
    WaitingApproval,
    WaitingTakeover,
)
from kinby.core.coding_clients import own_tokens
from kinby.core.errors import (
    BudgetExceeded,
    FactoryNotFound,
    FactoryRunNotFound,
    InvalidWorkItem,
    NotAnEarlierStep,
    NotAnIntake,
    RepositoryCommandsMissing,
    RunAwaitsNoAnswer,
    RunAwaitsNoApproval,
    RunNeedsNoHuman,
    TakeoverNotFound,
)
from kinby.factories.file import (
    BASE,
    BRANCH,
    FAILURE,
    NOTES,
    QUIET,
    AgentStep,
    ApproveStep,
    ClientStep,
    CodeStep,
    CommandStep,
    Duration,
    EndedCall,
    Engine,
    FactoryFile,
    InstanceName,
    InvalidFactoryFile,
    NeedsHumanCall,
    Outcome,
    Retry,
    SendBack,
    SignalFilter,
    Step,
    ToolCall,
    ValueType,
    WaitStep,
    WorkbenchAgentStep,
    WorkbenchCodeStep,
    WorkbenchCommandStep,
    WorkbenchFactory,
    WorkbenchStep,
    command_lists,
    duration_seconds,
    run_value_name,
    run_value_names,
    with_run_values,
    with_run_values_as_text,
)
from kinby.hub.factories import FactoryStore
from kinby.hub.models import Signal
from kinby.hub.registry import (
    STOPS,
    UNFINISHED,
    FactoryMember,
    HubRegistry,
    ManagedInstance,
    RunAskRef,
    RunStopRef,
)
from kinby.instance import ManifestError, repository_commands
from kinby.plugins.notify import approval_notification, takeover_notification

if TYPE_CHECKING:
    from kinby.hub.outbox import Outbox


@dataclass(frozen=True)
class UndeliveredCall:
    """A call the instance provably did not take, so it ran nothing (ADR 0089)."""

    reason: str


@dataclass(frozen=True)
class LostAnswer:
    """A call the instance took, whose answer a lost connection kept from the hub. The instance
    holds that answer, so the same call sent again gets it (ADR 0107)."""

    reason: str


#: Run one step in the instance with this id, and return its result, or why it went undelivered
#: or its answer was lost.
type StepCaller = Callable[
    [UUID, StepRunCommand], Awaitable[StepResult | UndeliveredCall | LostAnswer]
]


@dataclass(frozen=True)
class Untold:
    """Why the instance did not take a run's stop, which the hub tells it again."""

    reason: str


#: Tell the instance with this id that a run it handed in stopped. None once it took the stop.
type StopTeller = Callable[[UUID, RunStop], Awaitable[Untold | None]]
#: Have the workbench with this id remove the folder of a run that ended. Raises when it did not.
type FolderRemover = Callable[[UUID, RunFolderRemoveCommand], Awaitable[object]]
#: Write the view among the views of the instance with this id. Raises when it did not take it.
type ViewWriter = Callable[[UUID, View], Awaitable[object]]

_logger = logging.getLogger(__name__)

BACKOFF_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 300.0
#: How long a client step's coding client runs when the step declares no timeout.
_CLIENT_TIMEOUT: Duration = "60m"
#: The statuses a run ends in, which the factory's ended call follows.
_ENDS = (FactoryRunStatus.DONE, FactoryRunStatus.CANCELLED)
#: What a run worktree's branch keeps of each work item value. The rest becomes a dash.
_BRANCH_WORD = re.compile(r"[a-z0-9]+")
#: The provider of the models each coding client runs, as a line's ``provider:model`` names it.
_PROVIDERS = {CodingClient.CLAUDE: "anthropic", CodingClient.CODEX: "openai"}
#: The options a needs-human notification offers, a send back naming the step it goes to.
_RETRY = "Retry"
_SEND_BACK = "Send back to "
_CANCEL = "Cancel"


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
    then. None makes it on the workbench of the run's line.
    """

    run_id: UUID
    instance: InstanceName | None
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


@dataclass(frozen=True)
class _QueuedRemoval:
    """The folder of a run that ended, waiting for its workbench to remove it."""

    command: RunFolderRemoveCommand


type _Queued = _QueuedStep | _QueuedReport | _QueuedStop | _QueuedRemoval
#: A factory of either kind: its steps run in instances, or on a line's workbench.
type _Factory = FactoryFile | WorkbenchFactory
type _AnyStep = Step | WorkbenchStep


class FactoryRuns:
    """Start runs from intake, queue their steps per instance, and record every attempt."""

    def __init__(
        self,
        registry: HubRegistry,
        factories: FactoryStore,
        run_step: StepCaller,
        tell_stop: StopTeller,
        outbox: Outbox,
        remove_folder: FolderRemover,
        write_view: ViewWriter,
    ) -> None:
        self._registry = registry
        self._factories = factories
        self._run_step = run_step
        self._tell_stop = tell_stop
        self._outbox = outbox
        self._remove_folder = remove_folder
        self._write_view = write_view
        #: The steps, needs_human and ended calls and run stops waiting for each instance, and
        #: the steps and folder removals waiting for each workbench, by id.
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

    async def hand_in(self, line: Line, work_item: Mapping[ValueName, StepValue]) -> FactoryRun:
        """Start a run of the line's factory on its workbench, which the line's owner holds.

        A work item that matches an unfinished run of the line returns that run, and retries
        the step of one that needs a human.
        """
        factory = self._file(line.factory)
        if factory is None:
            raise FactoryNotFound(f'There is no factory "{line.factory}".')
        errors = _work_item_errors(factory.work_item, work_item)
        if errors:
            raise InvalidWorkItem(errors)
        run, started = self._registry.open_run(
            line.owner_id, line.factory, work_item, factory.steps[0].id, line.line_id
        )
        if started:
            self._publish(run)
            self._enqueue(run)
        elif run.status is FactoryRunStatus.NEEDS_HUMAN:
            return self._restart(run, run.step)
        return run

    async def list(self, command: FactoryRunListCommand) -> FactoryRunListResult:
        """One page of the runs the command's filters match, oldest first."""
        runs = self._registry.runs(
            command.factory,
            None if command.status is None else (command.status,),
            command.instance_id,
            line_id=command.line_id,
            after=command.cursor,
        )
        page = runs[: command.limit]
        return FactoryRunListResult(
            runs=page, cursor=page[-1].run_id if len(runs) > len(page) else None
        )

    def rework(self, run: FactoryRun) -> Rework:
        """The run's attempts per step, and its retries and send-backs by the tally rules.

        Its last attempt was not tried again or sent back yet, whatever it ended in. Without the
        factory's file, no attempt reads as either.
        """
        attempts = self._registry.attempts(run.run_id)
        factory = self._file(run.factory)
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
            earlier_steps=_earlier(self._file(run.factory), run.step),
        )

    async def retry(self, command: FactoryRunRetryCommand) -> FactoryRun:
        """Try the step the run stopped at again, with the step's retries reset."""
        run = _needing_human(self._existing(command.run_id))
        return self._restart(run, run.step)

    async def send_back(self, command: FactoryRunSendBackCommand) -> FactoryRun:
        """Send the run back to a step before the one it stopped or awaits approval at, with the
        user's notes or none.

        Its retries and send-backs count afresh.
        """
        run = self._existing(command.run_id)
        factory = self._file(run.factory)
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
        moved = self._registry.send_back_run(run.run_id, command.step, command.notes)
        self._publish(moved)
        self._enqueue(moved)
        return moved

    async def approve(self, command: FactoryRunApproveCommand) -> FactoryRun:
        """Move a run that awaits the user's approval on, as its approve step's next outcome."""
        run = self._existing(command.run_id)
        if not _awaits_approval(self._file(run.factory), run):
            raise RunAwaitsNoApproval(
                f'Factory run "{run.run_id}" is {run.status.value}, so it awaits no approval.'
            )
        approved = StepResult(ending=StepEnding.CLEAN, summary="The user approved it.")
        return self._advance(run.run_id, approved)

    async def answer(self, command: FactoryRunAnswerCommand) -> FactoryRun:
        """Resume the turn the run's kinby step parked on its ask with the user's answer, as the
        step's next attempt on its workbench's queue."""
        run = self._existing(command.run_id)
        ask = self._parked_ask(run)
        if not isinstance(ask, WaitingApproval) or ask.request.approval_id != command.approval_id:
            raise RunAwaitsNoAnswer(
                f'Factory run "{run.run_id}" is {run.status.value}, and waits for no answer to '
                f'ask "{command.approval_id}".'
            )
        moved = self._registry.answer_ask(run.run_id, command.answer)
        self._publish(moved)
        self._enqueue(moved)
        return moved

    async def finish_takeover(self, command: FactoryRunTakeoverFinishCommand) -> FactoryRun:
        """Resume the turn the run's kinby step parked on the takeover the user finished, as the
        step's next attempt on its workbench's queue."""
        run = self._existing(command.run_id)
        ask = self._parked_ask(run)
        if not isinstance(ask, WaitingTakeover) or ask.request.takeover_id != command.takeover_id:
            raise TakeoverNotFound(
                f'Factory run "{run.run_id}" is {run.status.value}, and waits for no takeover '
                f'"{command.takeover_id}".'
            )
        moved = self._registry.answer_ask(run.run_id, StepAnswer(decision=ApprovalDecision.APPROVE))
        self._publish(moved)
        self._enqueue(moved)
        return moved

    async def answer_ask(self, ask: RunAskRef, decision: ApprovalDecision) -> bool:
        """Answer the run's ask as the user's reply to its notification says. False when the run
        no longer waits for that answer."""
        command = FactoryRunAnswerCommand(
            run_id=ask.run_id,
            approval_id=ask.approval.request.approval_id,
            answer=StepAnswer.model_validate({"decision": decision}),
        )
        try:
            await self.answer(command)
        except FactoryRunNotFound, RunAwaitsNoAnswer:
            return False
        return True

    def signal_accepted(self, instance_id: UUID, signal: Signal) -> None:
        """Move on each run of the instance's factory, or of the lines it owns, whose parked wait
        the signal matches.

        A run still working toward a wait the signal matches moves on from it as soon as it
        parks there, so a signal that lands between two steps is not lost. A run that needs a
        human ends once one of the factory's closing signals says its work finished elsewhere.
        The instance of the factory's intake moves on only the runs it handed in.
        """
        membership = self._membership(instance_id)
        if membership is not None:
            member, factory = membership
            owner = instance_id if member.name == factory.intake.instance else None
            self._signalled(factory, self._registry.runs(member.factory, UNFINISHED, owner), signal)
        for line in self._registry.lines():
            factory = self._file(line.factory)
            if line.owner_id == instance_id and factory is not None:
                runs = self._registry.runs(line.factory, UNFINISHED, line_id=line.line_id)
                self._signalled(factory, runs, signal)

    def _signalled(
        self, factory: _Factory, unfinished: Sequence[FactoryRun], signal: Signal
    ) -> None:
        """Move on, or end, each of the factory's *unfinished* runs the signal matches.

        *unfinished* is read before any parked run moves on, so the signal wakes each run at
        most once.
        """
        body = _json(signal.body)
        waits = [step for step in factory.steps if isinstance(step, WaitStep)]
        for run in unfinished:
            step = _step(factory, run.step) if run.step is not None else None
            if run.status is not FactoryRunStatus.PARKED or not isinstance(step, WaitStep):
                continue
            held = self._held(run, factory, step.signal)
            if any(_matches(fields, signal, body, held) for fields in step.signal):
                self._disarm(run.run_id)
                summary = f'A signal on routine "{signal.routine}" matched.'
                self._advance(run.run_id, StepResult(ending=StepEnding.CLEAN, summary=summary))
        working = [FactoryRunStatus.QUEUED, FactoryRunStatus.RUNNING]
        for run in (run for run in unfinished if run.status in working):
            held = self._held(run, factory, [fields for wait in waits for fields in wait.signal])
            for wait in waits:
                if any(_matches(fields, signal, body, held) for fields in wait.signal):
                    self._woken.setdefault(run.run_id, set()).add(wait.id)
        closing = factory.closing
        for run in (run for run in unfinished if run.status is FactoryRunStatus.NEEDS_HUMAN):
            held = self._held(run, factory, [*closing.done, *closing.cancelled])
            if any(_matches(fields, signal, body, held) for fields in closing.done):
                self._move(run.run_id, FactoryRunStatus.DONE, None)
            elif any(_matches(fields, signal, body, held) for fields in closing.cancelled):
                self._move(run.run_id, FactoryRunStatus.CANCELLED, run.step)

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

    async def answer_stop(self, stop: RunStopRef, option: str) -> str | None:
        """Retry, send back or cancel the run as the user's reply to its needs-human notification
        says. What came of it, or None when the run moved since that stop."""
        run = self._registry.run(stop.run_id)
        if (
            run is None
            or run.status is not FactoryRunStatus.NEEDS_HUMAN
            or self._registry.stop_generation(run.run_id) != stop.generation
        ):
            return None
        if option == _RETRY:
            await self.retry(FactoryRunRetryCommand(run_id=run.run_id))
            return "Retried."
        if option == _CANCEL:
            await self.cancel(FactoryRunCancelCommand(run_id=run.run_id))
            return "Cancelled."
        step = option.removeprefix(_SEND_BACK)
        await self.send_back(FactoryRunSendBackCommand(run_id=run.run_id, step=step))
        return f'Sent back to "{step}".'

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
        until its plan window resets still wakes at the reset. One parked on an ask waits for the
        answer. A needs_human report, an ended call or a run's stop queued but not delivered
        before the hub stopped is queued again.

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
            factory = self._file(run.factory)
            step = (
                _step(factory, run.step) if factory is not None and run.step is not None else None
            )
            if run.wakes_at is not None:
                self._wake_at(run.run_id, run.step, run.wakes_at)
            elif isinstance(step, WaitStep):
                self._arm(run.run_id, step, self._registry.attempts(run.run_id)[-1].started_at)
        for run in pending:
            factory = self._file(run.factory)
            if factory is not None and factory.needs_human is not None:
                attempts = self._registry.attempts(run.run_id)
                summary = attempts[-1].summary if attempts else ""
                self._report(factory, factory.needs_human, run, summary)
        for run in uncalled:
            factory = self._file(run.factory)
            if factory is not None and factory.ended is not None:
                self._report(factory, factory.ended, run)
        for stop in untold:
            self._put_stop(_QueuedStop(stop))

    def needing_human(self, record: ManagedInstance, factory: FactoryFile | None) -> int:
        """How many runs of the instance's factory, whose file is *factory*, need the user at a
        step in that instance.

        A run needs the user when it stopped for a human or awaits approval. A step that runs in
        no instance counts toward the instance of the factory's intake. A run counts only toward
        the instance its step runs in: the intake's instance for the runs it handed in, and the
        first active instance of any other role.
        """
        member = record.factory
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
            for run in self._registry.runs(
                member.factory, (FactoryRunStatus.NEEDS_HUMAN, FactoryRunStatus.PARKED), owner
            )
            if run.status is FactoryRunStatus.NEEDS_HUMAN or _awaits_approval(factory, run)
        )

    def missing_commands(
        self, record: ManagedInstance, factory: FactoryFile | None
    ) -> Sequence[CommandListName]:
        """The lists of repository commands the instance's role in *factory* needs and it does not
        hold."""
        member = record.factory
        if member is None or factory is None:
            return []
        return _missing_commands(factory, member.name, record.path)

    def held_commands(self, record: ManagedInstance) -> Sequence[RepositoryCommandList]:
        """The lists of repository commands the instance holds now, when its factory role needs
        any."""
        member = record.factory
        factory = self.factory(member.factory) if member is not None else None
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
        factory = self.factory(member.factory) if member is not None else None
        if member is None or factory is None:
            return None
        return member, factory

    def _parked_ask(self, run: FactoryRun) -> WaitingApproval | WaitingTakeover | None:
        """The ask the run's kinby turn is parked on, or None when it is parked on none."""
        last = self._registry.attempts(run.run_id)[-1:]
        if run.status is not FactoryRunStatus.PARKED or not last:
            return None
        return last[0].ask if last[0].answer is None else None

    def _intake_instance(self, name: FactoryName) -> InstanceName | None:
        factory = self.factory(name)
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
        """The instance or workbench the run's step runs on and what it is asked, or why it cannot
        run.

        A wait or approve step runs in no instance: it is the step itself, which parks the run.
        An approve step comes with the run's values filled in its summary.
        """
        factory = self._file(run.factory)
        step = _step(factory, step_id) if factory is not None else None
        if factory is None or step is None:
            return _Refused(step_id, f'Factory "{run.factory}" has no step "{step_id}" any more.')
        results = self._results(run.run_id, factory, step.id)
        held = run.work_item | results
        unmet = _unmet(factory, step, held)
        if unmet is not None:
            return unmet
        if isinstance(step, ApproveStep):
            return replace(step, summary=with_run_values_as_text(step.summary, held))
        if isinstance(step, WaitStep):
            return step
        if isinstance(step, WorkbenchAgentStep | WorkbenchCommandStep | WorkbenchCodeStep):
            return self._on_workbench(run, factory, step, held, results)
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
        instance_id = self.holder(run, step.instance)
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

    def _on_workbench(
        self,
        run: FactoryRun,
        factory: _Factory,
        step: WorkbenchAgentStep | WorkbenchCommandStep | WorkbenchCodeStep,
        held: Mapping[ValueName, StepValue],
        results: dict[ValueName, StepValue],
    ) -> tuple[UUID, _QueuedStep] | _Refused:
        """The workbench of the run's line and what it is asked, in the run's folder there.

        A command step's lists of repository commands are the workbench's lists now. The first
        step that works in the folder has it cut again once the run went back to it from a later
        one, so the run starts over from the base. A retry of that step keeps the folder. A
        factory's worktree call names the run worktree's branch and base again for that cut, and
        first for a run that holds none yet, and the folder is cut fresh from that base either
        way. An agent step fails once what the line's runs cost today reached its daily budget.
        """
        line, placed = self._line(run), self._workbench(run)
        if line is None or placed is None:
            return _Refused(step.id, f'The line of factory run "{run.run_id}" is gone.')
        workbench, folder = placed
        assert workbench.workbench is not None
        match step:
            case WorkbenchAgentStep():
                limit, today = line.usd_per_day, datetime.now(UTC).date()
                if limit is not None and self._registry.line_cost(line.line_id, today) >= limit:
                    return _Refused(step.id, str(BudgetExceeded("usd_per_day", limit)))
                agent_run = (
                    self._kinby_run(run, step, line.model)
                    if step.engine is Engine.KINBY
                    else self._client_run(run, factory, step, held, line.model)
                )
                if isinstance(agent_run, _Refused):
                    return agent_run
                asked = agent_run
            case WorkbenchCommandStep():
                commands: list[str] = []
                for entry in step.run:
                    if isinstance(entry, str):
                        commands.append(with_run_values(entry, held))
                    elif entry.commands in workbench.workbench.commands:
                        commands.extend(workbench.workbench.commands[entry.commands])
                    else:
                        return _Refused(
                            step.id,
                            f'Workbench "{workbench.manifest_id}" holds no repository commands '
                            f'named "{entry.commands}".',
                        )
                timeout = duration_seconds(step.timeout) if step.timeout is not None else None
                asked = CommandStepRun(run=commands, timeout_seconds=timeout)
            case WorkbenchCodeStep():
                asked = CodeStepRun(call=step.call)
        fresh = step is _first_at_work(factory) and self._went_on(run.run_id, step.id)
        naming = factory.worktree if isinstance(factory, WorkbenchFactory) else None
        if (
            naming is not None
            and workbench.workbench.workspace_source is not None
            and (fresh or folder.branch is None)
        ):
            # A branch the factory names may hold an abandoned run's commits, so naming cuts it
            # fresh from the base the factory names with it.
            folder, fresh = RunFolder(branch=None, named_by=naming.call), True
        command = StepRunCommand(
            step=asked,
            origin=FactoryRunOrigin(factory=run.factory, run_id=run.run_id, step=step.id),
            hook=step.check,
            work_item=run.work_item,
            results=results,
            folder=folder,
            fresh_folder=fresh,
        )
        return workbench.instance_id, _QueuedStep(run.run_id, step.id, command)

    def _went_on(self, run_id: UUID, step: StepId) -> bool:
        """Whether the run made an attempt at another step since its last attempt at *step*."""
        attempts = [attempt.step for attempt in self._registry.attempts(run_id)]
        return step in attempts and attempts[-1] != step

    def _line(self, run: FactoryRun) -> Line | None:
        return next((line for line in self._registry.lines() if line.line_id == run.line_id), None)

    def _workbench(self, run: FactoryRun) -> tuple[ManagedInstance, RunFolder] | None:
        """The workbench of the run's line and the run's folder there, or None without either.

        On a workbench whose workspace is a git clone the folder is the run worktree of the branch
        the factory's worktree call last named, from the base it named. Before it named one, it is
        a directory named for the run. A factory with no worktree call has the branch named for the
        factory, the work item and the run, since two work items can read alike once lowercased.
        On any other workbench the folder is a directory named for the run.
        """
        line = self._line(run)
        workbench = self._registry.instance(line.workbench_id) if line is not None else None
        if workbench is None or workbench.workbench is None:
            return None
        if workbench.workbench.workspace_source is None:
            return workbench, RunFolder(branch=None)
        factory = self._file(run.factory)
        if isinstance(factory, WorkbenchFactory) and factory.worktree is not None:
            named = self._named(run.run_id, factory)
            if not named:
                return workbench, RunFolder(branch=None)
            return workbench, RunFolder(branch=str(named[BRANCH]), base=str(named[BASE]))
        words = [
            word
            for name, value in run.work_item.items()
            for word in _BRANCH_WORD.findall(f"{name} {value}".lower())
        ]
        branch = "-".join((*words, run.run_id.hex[:8]))
        return workbench, RunFolder(branch=f"{run.factory}/{branch}")

    def holder(self, run: FactoryRun, instance: InstanceName) -> UUID | None:
        """The id of the instance that holds role *instance* for the run, or None when none does.

        The instance that handed the run in holds its own role for it, and no other instance of
        that role does, since one may work on another repository. None does once that instance
        was converted into a workbench.
        """
        owner = self._registry.instance(run.instance_id)
        if owner is not None and owner.workbench is not None:
            return None
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

    def _prompt(
        self, run: FactoryRun, step: AgentStep | ClientStep | WorkbenchAgentStep
    ) -> str | _Refused:
        try:
            return self._factories.prompt(run.factory, step.prompt)
        except (FactoryNotFound, OSError) as exc:
            return _Refused(step.id, f'Prompt "{step.prompt}" cannot be read: {exc}')

    def _client_run(
        self,
        run: FactoryRun,
        factory: _Factory,
        step: ClientStep | WorkbenchAgentStep,
        held: Mapping[ValueName, StepValue],
        line_model: str | None = None,
    ) -> ClientStepRun | _Refused:
        """What the client step asks its instance to run, or the agent step its workbench.

        An agent step that names no model takes its line's, *line_model*, when its coding client
        runs that model's provider, and the client's default otherwise. When the step it resumes
        recorded no session, that step fails an attempt, as it would for a value it should have
        produced.
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
        client = step.client if isinstance(step, ClientStep) else CodingClient(step.engine)
        provider, _, name = (line_model or "").partition(":")
        return ClientStepRun(
            client=client,
            prompt=prompt,
            resume=session,
            timeout_seconds=duration_seconds(step.timeout or _CLIENT_TIMEOUT),
            model=step.model or (name if provider == _PROVIDERS[client] else None),
            effort=step.effort,
            branch=_branch(step, held) if isinstance(step, ClientStep) else None,
        )

    def _kinby_run(
        self, run: FactoryRun, step: WorkbenchAgentStep, line_model: str | None
    ) -> AgentStepRun | _Refused:
        """The turn the agent step asks its workbench to run, with its model or else its line's.
        The kinby engine runs any provider's model, and keeps no timeout.

        After the user answered the ask the step's last attempt parked on, the turn resumes with
        that answer.
        """
        model = step.model or line_model
        if model is None:
            return _Refused(
                step.id,
                f'Step "{step.id}" runs on the kinby engine, and it and its line name no model.',
            )
        prompt = self._prompt(run, step)
        if isinstance(prompt, _Refused):
            return prompt
        last = self._registry.attempts(run.run_id)[-1:]
        answered = (
            AnsweredAsk(ask=last[0].ask, answer=last[0].answer)
            if last and last[0].ask is not None and last[0].answer is not None
            else None
        )
        return AgentStepRun(prompt=prompt, model=model, answered=answered)

    def _session(self, run_id: UUID, factory: _Factory, step: StepId) -> CodingSessionId | None:
        """The newest coding client session recorded by any step that shares the step's session.

        Steps share a session when their resumes lead back to the same step. A step that resumes
        a lost session runs in a new one, which the steps after it continue.
        """
        resumes = {
            other.id: other.resume
            for other in factory.steps
            if isinstance(other, ClientStep | WorkbenchAgentStep)
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

    def _held(
        self, run: FactoryRun, factory: _Factory, filters: Iterable[SignalFilter]
    ) -> Mapping[ValueName, StepValue]:
        """The run's values that *filters* may name. Its attempts are read only when one names a
        value."""
        named = any(
            run_value_name(value) is not None for fields in filters for value in fields.values()
        )
        return run.work_item | self._results(run.run_id, factory) if named else {}

    def _results(
        self, run_id: UUID, factory: _Factory, starting: StepId | None = None
    ) -> dict[ValueName, StepValue]:
        """The values the run's clean attempts, and its failures sent back, recorded on its latest
        pass through the steps.

        A later value overrides an earlier one. An attempt at a step, like *starting* one, drops
        what that step and every step after it recorded before, so a run sent back holds no value
        from the pass it left, save what the step that sent it back recorded: those values go
        with the work to the step it went back to, as if that step's pass held them. Trying a
        step again after an attempt at it that recorded nothing drops nothing, so a retry holds
        what the attempt before it held. A failed attempt that runs its step again under
        ``failed`` adds what it recorded. The branch and base the factory's worktree call last
        named are ``branch`` and ``base``, and the notes the user last sent the run back with are
        ``notes``.
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
            index = order.get(attempt.step, -1)
            if attempt.ending is StepEnding.CLEAN or _failed_to(factory, attempt) is not None:
                recorded |= {name: (index, value) for name, value in attempt.values.items()}
                last, retrying = (index, attempt.values), None
            elif _runs_again(factory, attempt):
                recorded |= {name: (index, value) for name, value in attempt.values.items()}
        if starting is not None:
            start(starting)
        results = {name: value for name, (_, value) in recorded.items()}
        results |= self._named(run_id, factory)
        notes = self._registry.run_notes(run_id)
        return results if notes is None else results | {NOTES: notes}

    def _named(self, run_id: UUID, factory: _Factory) -> dict[ValueName, StepValue]:
        """The ``branch`` and ``base`` the factory's worktree call last named for the run, however
        that attempt ended, or none before it named them."""
        if not isinstance(factory, WorkbenchFactory) or factory.worktree is None:
            return {}
        for attempt in reversed(self._registry.attempts(run_id)):
            branch, base = attempt.values.get(BRANCH), attempt.values.get(BASE)
            if isinstance(branch, str) and isinstance(base, str):
                return {BRANCH: branch, BASE: base}
        return {}

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
                case _QueuedRemoval(command=command):
                    await self._remove(instance_id, command)

    async def _remove(self, workbench_id: UUID, command: RunFolderRemoveCommand) -> None:
        """Have the workbench remove the folder of a run that ended. One it could not remove
        stays, and the log says so."""
        try:
            await self._remove_folder(workbench_id, command)
        except Exception:
            _logger.warning(
                "The folder of factory run %s stays on its workbench, which did not remove it.",
                command.run_id,
                exc_info=True,
            )

    async def _deliver(self, instance_id: UUID, queued: _QueuedStep) -> None:
        """Send the step until its instance answers it.

        A call the instance did not take is no attempt. One whose answer was lost keeps its
        attempt open and is sent again as it was, for the answer the instance holds.
        """
        backoff = 0.0
        command: StepRunCommand | None = None
        while True:
            if command is None:
                self._publish(self._registry.begin_attempt(queued.run_id, queued.step))
                attempt = self._registry.attempts(queued.run_id)[-1].attempt
                command = queued.command.model_copy(update={"attempt": attempt})
            answer = await self._run_step(instance_id, command)
            if isinstance(answer, StepResult):
                answer = await self._views_written(queued.run_id, answer)
                self._advance(queued.run_id, self._own_usage(queued.run_id, answer))
                return
            if isinstance(answer, UndeliveredCall):
                self._publish(self._registry.drop_attempt(queued.run_id))
                command = None
            backoff = _next_backoff(backoff)
            _logger.info(
                "Step %s of factory run %s got no answer from its instance, sending it again in "
                "%s seconds: %s",
                queued.step,
                queued.run_id,
                backoff,
                answer.reason,
            )
            await asyncio.sleep(backoff)
            record = self._registry.instance(instance_id)
            if record is None or not record.active:
                if command is not None:
                    self._publish(self._registry.drop_attempt(queued.run_id))
                self._enqueue(self._existing(queued.run_id))
                return

    async def _views_written(self, run_id: UUID, result: StepResult) -> StepResult:
        """*result* with the views it wrote that the run's instance, the owner of its line, took
        among its views. Each one it did not take is named after the summary."""
        instance_id = self._existing(run_id).instance_id
        taken: list[View] = []
        for view in result.views:
            try:
                await self._write_view(instance_id, view)
            except Exception as exc:
                reason = f'The owner did not take view "{view.id}": {exc or type(exc).__name__}'
                result = _noted(result, reason, result.ending)
            else:
                taken.append(view)
        return result.model_copy(update={"views": taken})

    def _own_usage(self, run_id: UUID, result: StepResult) -> StepResult:
        """*result* with its coding client's run counting only its own tokens: what the run's
        earlier attempts in its session counted comes off."""
        usage = result.usage
        if usage is None or usage.delegated_run is None:
            return result
        earlier = (
            attempt.usage.delegated_run
            for attempt in self._registry.attempts(run_id)
            if attempt.usage is not None and attempt.usage.delegated_run is not None
        )
        own = own_tokens(usage.delegated_run, earlier)
        return result.model_copy(update={"usage": usage.model_copy(update={"delegated_run": own})})

    async def _send_report(self, instance_id: UUID, report: _QueuedReport) -> None:
        if self._stale(report):
            return
        match await self._run_step(instance_id, report.command):
            case StepResult(ending=StepEnding.CLEAN) if report.status in _ENDS:
                self._registry.mark_ended_called(report.run_id)
                self._drop_folder(self._existing(report.run_id))
                return
            case StepResult(ending=StepEnding.CLEAN):
                self._registry.mark_reported(report.run_id, report.generation)
                return
            case (
                StepResult(summary=reason)
                | UndeliveredCall(reason=reason)
                | LostAnswer(reason=reason)
            ):
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

        An approval's attempt holds its summary, and tells the user. A wait a signal already
        matched on the run's way to it moves on at once.
        """
        summary = step.summary if isinstance(step, ApproveStep) else ""
        parked = self._registry.begin_attempt(run_id, step.id, FactoryRunStatus.PARKED, summary)
        self._publish(parked)
        if isinstance(step, ApproveStep):
            self._notify_approval(parked, step.summary)
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
        one parks the run until its plan window resets, and an asked one until the user answers.
        """
        run = self._registry.run(run_id)
        if run is None or run.step is None:
            raise FactoryRunNotFound(f'Factory run "{run_id}" has no step to settle.')
        if result.resets_at is not None:
            self._registry.end_attempt(run_id, result)
            return self._park_until(run_id, run.step, result.resets_at)
        if result.ask is not None:
            self._registry.end_attempt(run_id, result)
            parked = self._move(run_id, FactoryRunStatus.PARKED, run.step)
            self._notify_ask(parked, result.ask)
            return parked
        factory = self._file(run.factory)
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
        stops it. A run that ends done or cancelled gets the factory's ended call, and a line's
        run then loses its folder."""
        factory = self._file(self._existing(run_id).factory)
        ended = factory.ended if factory is not None and status in _ENDS else None
        moved = self._registry.move_run(run_id, status, step, ended_call=ended is not None)
        self._publish(moved)
        if status in _ENDS and ended is None:
            self._drop_folder(moved)
        if moved.status in STOPS:
            generation = self._registry.stop_generation(run_id)
            self._put_stop(_QueuedStop(self._registry.run_stop(run_id, generation)))
            if moved.status is FactoryRunStatus.NEEDS_HUMAN:
                self._notify(moved, RunStopRef(run_id, generation), factory)
        if factory is not None and ended is not None:
            self._report(factory, ended, moved)
        return moved

    def _drop_folder(self, run: FactoryRun) -> None:
        """Have the workbench of the run's line remove the run's folder, after the work it
        has queued."""
        placed = self._workbench(run)
        if placed is not None:
            workbench, folder = placed
            removal = RunFolderRemoveCommand(run_id=run.run_id, folder=folder)
            self._queue(workbench.instance_id).put_nowait(_QueuedRemoval(removal))

    def _notify(self, run: FactoryRun, stop: RunStopRef, factory: _Factory | None) -> None:
        """Tell the user the run needs them, offering to retry it, send it back to the step before
        its stop, or cancel it."""
        if self._registry.instance(run.instance_id) is None:
            return
        send_back = [f"{_SEND_BACK}{step}" for step in _earlier(factory, run.step)[-1:]]
        self._outbox.notify(
            run.instance_id,
            f"run_stop:{run.run_id}:{stop.generation}",
            source=run.factory,
            title="Needs human",
            text=f'The run for {_work_item(run)} stopped at "{run.step}".',
            page="/library",
            options=(_RETRY, *send_back, _CANCEL),
            stop=stop,
        )

    def _notify_approval(self, run: FactoryRun, summary: str) -> None:
        """Tell the user the run awaits their approval of what its approve step sums up."""
        if self._registry.instance(run.instance_id) is None:
            return
        attempt = self._registry.attempts(run.run_id)[-1].attempt
        self._outbox.notify(
            run.instance_id,
            f"run_approval:{run.run_id}:{run.step}:{attempt}",
            source=run.factory,
            title="Awaits approval",
            text=f'The run for {_work_item(run)} awaits approval at "{run.step}": {summary}',
            page="/library",
        )

    def _notify_ask(self, run: FactoryRun, ask: WaitingApproval | WaitingTakeover) -> None:
        """Ask the user to approve or deny the call the run's turn parked on, or to take over its
        browser, which they finish on the hub."""
        if self._registry.instance(run.instance_id) is None:
            return
        if isinstance(ask, WaitingTakeover):
            taking_over = takeover_notification(run.factory, ask.request)
            self._outbox.notify(
                run.instance_id,
                f"run_takeover:{run.run_id}:{ask.request.takeover_id}",
                source=run.factory,
                title=taking_over.title,
                text=f"The run for {_work_item(run)} asks you to take over its browser at "
                f'"{run.step}": {taking_over.text}',
                page="/library",
            )
            return
        raised = approval_notification(run.factory, ask.turn_id, ask.request, in_routine=False)
        self._outbox.notify(
            run.instance_id,
            f"run_ask:{run.run_id}:{ask.request.approval_id}",
            source=run.factory,
            title=raised.title,
            text=f'The run for {_work_item(run)} asks at "{run.step}": {raised.text}',
            page="/library",
            options=tuple(raised.options),
            ask=RunAskRef(run.run_id, ask),
        )

    def _put_stop(self, queued: _QueuedStop) -> None:
        owner = self._registry.instance(self._existing(queued.stop.run_id).instance_id)
        if owner is None or not owner.active or owner.workbench is not None:
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
        factory: _Factory,
        call: NeedsHumanCall | EndedCall | ToolCall,
        run: FactoryRun,
        summary: str | None = None,
    ) -> None:
        """Queue the factory's call for the run as it stopped: the needs_human call, with the
        *summary* it stopped with, or the ended call. A line's run makes it on its workbench, in
        its folder.

        A run that ended done is at no step, so the call names the step of its last attempt.
        """
        step = run.step if run.step is not None else self._registry.attempts(run.run_id)[-1].step
        placed = self._workbench(run)
        command = StepRunCommand(
            step=CodeStepRun(call=call.call, summary=summary),
            origin=FactoryRunOrigin(factory=run.factory, run_id=run.run_id, step=step),
            work_item=run.work_item,
            results=self._results(run.run_id, factory),
            folder=placed[1] if placed is not None else None,
        )
        role = call.instance if isinstance(call, NeedsHumanCall | EndedCall) else None
        generation = self._registry.stop_generation(run.run_id)
        self._put_report(_QueuedReport(run.run_id, role, command, run.status, generation))

    def _put_report(self, report: _QueuedReport) -> None:
        factory = report.command.origin.factory
        run = self._existing(report.run_id)
        if report.instance is None:
            placed = self._workbench(run)
            instance_id = placed[0].instance_id if placed is not None and placed[0].active else None
        else:
            instance_id = self.holder(run, report.instance)
        if instance_id is None:
            _logger.warning(
                "Factory run %s stopped, and neither its line's workbench nor an instance of "
                "role %s of factory %s is there to make its %s call.",
                report.run_id,
                report.instance,
                factory,
                report.key,
            )
            return
        self._queue(instance_id).put_nowait(report)

    def factory(self, name: FactoryName) -> FactoryFile | None:
        """The file of a factory whose steps run in instances, as the hub keeps it, or None once
        the factory is gone."""
        found = self._file(name)
        return found if isinstance(found, FactoryFile) else None

    def _file(self, name: FactoryName) -> _Factory | None:
        """The factory's file as the hub keeps it, or None once the factory is gone."""
        try:
            return self._factories.file(name)
        except FactoryNotFound, InvalidFactoryFile:
            return None

    def _publish(self, run: FactoryRun) -> None:
        for subscriber in self._subscribers:
            subscriber.put_nowait(run)


def _step(factory: _Factory, step_id: StepId) -> _AnyStep | None:
    return next((step for step in factory.steps if step.id == step_id), None)


def _first_at_work(factory: _Factory) -> _AnyStep | None:
    """The factory's first step that works in the run's folder, past any wait or approval."""
    return next(
        (step for step in factory.steps if not isinstance(step, WaitStep | ApproveStep)), None
    )


def _work_item(run: FactoryRun) -> str:
    """The run's work item, as a notification names the run."""
    return ", ".join(f"{name} {value}" for name, value in run.work_item.items())


def _needing_human(run: FactoryRun) -> FactoryRun:
    if run.status is not FactoryRunStatus.NEEDS_HUMAN:
        raise RunNeedsNoHuman(
            f'Factory run "{run.run_id}" is {run.status.value}, so it needs no human.'
        )
    return run


def _awaits_approval(factory: _Factory | None, run: FactoryRun) -> bool:
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


def _earlier(factory: _Factory | None, step_id: StepId | None) -> list[StepId]:
    """The ids of the steps before *step_id* in *factory*."""
    ids = [step.id for step in factory.steps] if factory is not None else []
    return ids[: ids.index(step_id)] if step_id in ids else []


def _branch(step: ClientStep | CommandStep, held: Mapping[ValueName, StepValue]) -> str | None:
    """The run's value of the branch the step names, or None when it names none."""
    name = None if step.branch is None else run_value_name(step.branch)
    return None if name is None else str(held[name])


def _unmet(
    factory: _Factory, step: _AnyStep, held: Mapping[ValueName, StepValue]
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
    factory: _Factory,
    step: _AnyStep,
    result: StepResult,
    held: Mapping[ValueName, StepValue],
    tally: _Tally,
) -> tuple[StepResult, FactoryRunStatus, StepId | None]:
    """The result the step's attempt records, and the status and step it moves the run to.

    A clean result goes where its outcome sends it, and leaves the run for a human when it would
    send the work back along an edge more than that edge's max. One that names an outcome the
    step does not declare fails. A failed result at a step that declares what its failures do
    records what failed as ``failure``. It goes where the step sends its failures, up to that
    edge's max. Any other failed result is tried again while the step's retries last. A
    timed-out one, or one that needs a human, never is.
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
    if result.ending is StepEnding.FAILED and isinstance(step.failed, SendBack):
        back, most = step.failed.back, step.failed.max
        if tally.sent_back[step.id, back] >= most:
            reason = f'Step "{step.id}" sent its failures back to "{back}" {most} times, its max.'
            return _noted(result, reason), FactoryRunStatus.NEEDS_HUMAN, step.id
        return result, FactoryRunStatus.QUEUED, back
    never_again = result.ending in (StepEnding.TIMED_OUT, StepEnding.NEEDS_HUMAN)
    if never_again or tally.failures[step.id] >= _retries(step):
        return result, FactoryRunStatus.NEEDS_HUMAN, step.id
    return result, FactoryRunStatus.QUEUED, step.id


def _outcome(step: _AnyStep, name: str | None) -> Outcome | None:
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


def _tally(factory: _Factory, attempts: Iterable[StepAttempt]) -> _Tally:
    tally = _Tally(Counter(), Counter())
    for attempt in attempts:
        if attempt.ending is StepEnding.CLEAN:
            step = _step(factory, attempt.step)
            outcome = _outcome(step, attempt.outcome) if step is not None else None
            if isinstance(outcome, SendBack):
                tally.sent_back[attempt.step, outcome.back] += 1
        elif (failed_to := _failed_to(factory, attempt)) is not None:
            tally.sent_back[attempt.step, failed_to.back] += 1
        elif attempt.ending not in (None, StepEnding.LIMITED, StepEnding.ASKED):
            tally.failures[attempt.step] += 1
    return tally


def _failed_to(factory: _Factory, attempt: StepAttempt) -> SendBack | None:
    """Where a failed attempt sends the work, when its step declares where its failures go."""
    step = _step(factory, attempt.step)
    failed = step.failed if step is not None and attempt.ending is StepEnding.FAILED else None
    return failed if isinstance(failed, SendBack) else None


def _runs_again(factory: _Factory, attempt: StepAttempt) -> bool:
    """Whether a failed attempt runs its step again under the step's ``failed``."""
    step = _step(factory, attempt.step)
    return (
        step is not None and attempt.ending is StepEnding.FAILED and isinstance(step.failed, Retry)
    )


def _checked_done(
    factory: _Factory, held: Mapping[ValueName, StepValue], last: StepResult
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


def _checked_types(step: _AnyStep, result: StepResult) -> StepResult:
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


def _retries(step: _AnyStep) -> int:
    """How many times a failed step is tried again (ADR 0076).

    Once for a step a model or a coding client runs, and never for any other unless it opts in.
    """
    if isinstance(step.failed, Retry):
        return step.failed.retry
    if step.retry is not None:
        return step.retry
    return 1 if isinstance(step, AgentStep | ClientStep | WorkbenchAgentStep) else 0


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
