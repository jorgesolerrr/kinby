"""The ask of a kinby agent step's turn parks its run and reaches the user as a notification, and
their answer, through the hub, resumes the turn as the step's next attempt."""

import asyncio
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from kinby.contracts import (
    FACTORY_RUN_ANSWER,
    FACTORY_RUN_CANCEL,
    NOTIFICATION_LIST,
    NOTIFICATION_REPLY,
    AgentStepRun,
    AnsweredAsk,
    ApprovalDecision,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunAnswerCommand,
    FactoryRunCancelCommand,
    FactoryRunStatus,
    GateRule,
    Line,
    Notification,
    NotificationListCommand,
    NotificationListResult,
    NotificationReplyCommand,
    StepAnswer,
    StepEnding,
    StepResult,
    WaitingApproval,
)
from kinby.hub import Hub
from tests.test_factory_runs import detail, settled
from tests.test_factory_waits import parked
from tests.test_hub import FakeControl, FakeImages, FakeRuntime, hub_client
from tests.test_hub_line_agents import _hub, _kinby
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_lines import _home
from tests.test_hub_workbenches import CEC_BACKEND

KINBY = _kinby("    model: anthropic:claude-opus-5-5\n")
KEYED = CEC_BACKEND.model_copy(
    update={"secrets": CEC_BACKEND.secrets | {"ANTHROPIC_API_KEY": SecretStr("sk-ant")}}
)


def _asked() -> StepResult:
    """The attempt of a turn the gate parked on its ask to write a draft."""
    request = ApprovalRequested(
        approval_id=uuid4(),
        name="write",
        arguments={"path": "draft.md"},
        rule=GateRule("write"),
    )
    return StepResult(
        ending=StepEnding.ASKED,
        summary="The turn asks to call write.",
        ask=WaitingApproval(turn_id=uuid4(), request=request),
    )


async def _asking(hub: Hub, runtime: FakeRuntime, issue: int = 12) -> tuple[Line, FactoryRun]:
    """A line with a run whose kinby step parked on its ask."""
    await _home(hub)
    line = await _line(hub_client(hub), runtime, KEYED)
    run = await _handed_in(hub_client(hub), line, issue)
    await parked(hub, run.run_id)
    return line, run


async def _answered(hub: Hub, run_id: UUID, ask: WaitingApproval, answer: StepAnswer) -> object:
    command = FactoryRunAnswerCommand(
        run_id=run_id, approval_id=ask.request.approval_id, answer=answer
    )
    return await hub_client(hub).call(FACTORY_RUN_ANSWER, command)


async def _notifications(hub: Hub, owner: UUID) -> list[Notification]:
    listed = await hub_client(hub).call(
        NOTIFICATION_LIST, NotificationListCommand(instance_id=owner)
    )
    assert isinstance(listed, NotificationListResult), listed
    return listed.notifications


def test_a_kinby_steps_ask_parks_its_run_and_raises_one_notification_naming_the_run(tmp_path):
    control = FakeControl()
    asked = _asked()
    control.step_results = [asked]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)

    async def scenario() -> None:
        line, run = await _asking(hub, runtime)
        found = await detail(hub, run.run_id)
        [notification] = await _notifications(hub, line.owner_id)

        assert (found.run.status, found.run.step) == (FactoryRunStatus.PARKED, "implement")
        assert [(a.step, a.ending, a.ask) for a in found.attempts] == [
            ("implement", StepEnding.ASKED, asked.ask)
        ]
        assert notification.title == "Approval waiting"
        assert notification.text == 'The run for issue 12 asks at "implement": write: path=draft.md'
        assert notification.options == ["Approve", "Deny"]

    asyncio.run(scenario())


def test_a_run_parked_on_an_ask_lets_another_run_on_its_workbench_take_its_step(tmp_path):
    control = FakeControl()
    control.step_results = [_asked()]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)

    async def scenario() -> None:
        line, asking = await _asking(hub, runtime)

        other = await _handed_in(hub_client(hub), line, 13)
        done = await settled(hub, other.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert (await detail(hub, asking.run_id)).run.status is FactoryRunStatus.PARKED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "answer",
    [
        StepAnswer(decision=ApprovalDecision.APPROVE),
        StepAnswer(decision=ApprovalDecision.DENY, reason="Keep the notes."),
    ],
    ids=["approve", "deny"],
)
def test_an_answer_resumes_the_asked_turn_as_the_steps_next_attempt(tmp_path, answer):
    control = FakeControl()
    asked = _asked()
    control.step_results = [asked]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)

    async def scenario() -> None:
        _, run = await _asking(hub, runtime)
        assert asked.ask is not None

        moved = await _answered(hub, run.run_id, asked.ask, answer)
        done = await settled(hub, run.run_id)

        assert isinstance(moved, FactoryRun), moved
        assert done.run.status is FactoryRunStatus.DONE
        [(_, first), (_, resumed), _] = control.steps
        assert resumed.step == AgentStepRun(
            prompt="Implement the issue.\n",
            model="anthropic:claude-opus-5-5",
            answered=AnsweredAsk(ask=asked.ask, answer=answer),
        )
        assert resumed.folder == first.folder
        assert [(a.step, a.attempt, a.ending, a.answer) for a in done.attempts] == [
            ("implement", 1, StepEnding.ASKED, answer),
            ("implement", 2, StepEnding.CLEAN, None),
            ("fix", 1, StepEnding.CLEAN, None),
        ]

    asyncio.run(scenario())


def test_an_asked_attempt_spends_no_retry(tmp_path):
    control = FakeControl()
    first, second = _asked(), _asked()
    control.step_results = [first, second, StepResult(ending=StepEnding.FAILED)]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)
    approve = StepAnswer(decision=ApprovalDecision.APPROVE)

    async def scenario() -> None:
        _, run = await _asking(hub, runtime)
        assert first.ask is not None and second.ask is not None
        await _answered(hub, run.run_id, first.ask, approve)
        async with asyncio.timeout(5):
            while len((await detail(hub, run.run_id)).attempts) < 2:
                await asyncio.sleep(0.01)
        await parked(hub, run.run_id)
        await _answered(hub, run.run_id, second.ask, approve)
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert [(a.step, a.ending) for a in done.attempts] == [
            ("implement", StepEnding.ASKED),
            ("implement", StepEnding.ASKED),
            ("implement", StepEnding.FAILED),
            ("implement", StepEnding.CLEAN),
            ("fix", StepEnding.CLEAN),
        ]

    asyncio.run(scenario())


def test_a_reply_to_the_asks_notification_answers_it(tmp_path):
    control = FakeControl()
    asked = _asked()
    control.step_results = [asked]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)

    async def scenario() -> None:
        line, run = await _asking(hub, runtime)
        [notification] = await _notifications(hub, line.owner_id)
        assert asked.ask is not None

        await hub_client(hub).call(
            NOTIFICATION_REPLY,
            NotificationReplyCommand(notification_id=notification.notification_id, option="Deny"),
        )
        async with asyncio.timeout(5):
            while (done := await settled(hub, run.run_id)).run.status is FactoryRunStatus.PARKED:
                await asyncio.sleep(0.01)

        assert done.run.status is FactoryRunStatus.DONE
        [_, (_, resumed), _] = control.steps
        assert isinstance(resumed.step, AgentStepRun)
        assert resumed.step.answered == AnsweredAsk(
            ask=asked.ask, answer=StepAnswer(decision=ApprovalDecision.DENY)
        )

    asyncio.run(scenario())


def test_a_run_parked_on_an_ask_stays_parked_through_a_hub_restart_and_an_answer_resumes_it(
    tmp_path,
):
    control = FakeControl()
    asked = _asked()
    control.step_results = [asked]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)
    approve = StepAnswer(decision=ApprovalDecision.APPROVE)

    run_id = asyncio.run(_asking(hub, runtime))[1].run_id
    hub.close()
    restarted = Hub(
        tmp_path / "hub",
        runtime=runtime,
        images=FakeImages(),
        control=control,
        shipped_factories=tmp_path / "shipped",
    )

    async def after_the_restart() -> None:
        await restarted.recover()
        still = await detail(restarted, run_id)
        assert asked.ask is not None

        await _answered(restarted, run_id, asked.ask, approve)
        done = await settled(restarted, run_id)

        assert still.run.status is FactoryRunStatus.PARKED
        assert [a.ask for a in still.attempts] == [asked.ask]
        assert done.run.status is FactoryRunStatus.DONE
        [_, (_, resumed), _] = control.steps
        assert isinstance(resumed.step, AgentStepRun)
        assert resumed.step.answered == AnsweredAsk(ask=asked.ask, answer=approve)

    asyncio.run(after_the_restart())


def test_cancelling_a_run_parked_on_an_ask_ends_it_and_a_later_answer_is_refused(tmp_path):
    control = FakeControl()
    asked = _asked()
    control.step_results = [asked]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)

    async def scenario() -> None:
        _, run = await _asking(hub, runtime)
        assert asked.ask is not None

        cancelled = await hub_client(hub).call(
            FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=run.run_id)
        )
        refused = await _answered(
            hub, run.run_id, asked.ask, StepAnswer(decision=ApprovalDecision.APPROVE)
        )

        assert isinstance(cancelled, FactoryRun), cancelled
        assert cancelled.status is FactoryRunStatus.CANCELLED
        assert isinstance(refused, ErrorEnvelope), refused
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert len(control.steps) == 1

    asyncio.run(scenario())


def test_an_answer_to_another_ask_than_the_one_the_run_parked_on_is_refused(tmp_path):
    control = FakeControl()
    control.step_results = [_asked()]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, KINBY)
    stale = _asked().ask
    assert stale is not None

    async def scenario() -> None:
        _, run = await _asking(hub, runtime)

        refused = await _answered(
            hub, run.run_id, stale, StepAnswer(decision=ApprovalDecision.APPROVE)
        )

        assert isinstance(refused, ErrorEnvelope), refused
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert (await detail(hub, run.run_id)).run.status is FactoryRunStatus.PARKED

    asyncio.run(scenario())
