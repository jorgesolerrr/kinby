"""A takeover a kinby agent step's turn asks for parks its run and reaches the user as a
notification that names the run. Finishing it through the hub resumes the turn as the step's
next attempt."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import (
    FACTORY_RUN_ANSWER,
    FACTORY_RUN_TAKEOVER_FINISH,
    AgentStepRun,
    AnsweredAsk,
    ApprovalDecision,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunAnswerCommand,
    FactoryRunStatus,
    FactoryRunTakeoverFinishCommand,
    StepAnswer,
    StepEnding,
    StepResult,
    TakeoverRequested,
    WaitingTakeover,
)
from kinby.hub import Hub
from tests.test_factory_runs import detail, settled
from tests.test_factory_waits import parked
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_browser import FakeBrowserRuntime
from tests.test_hub_factories import write_factory
from tests.test_hub_line_asks import KEYED, KINBY, _notifications
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_lines import _home

BROWSING = KEYED.model_copy(update={"browser": True})


def _taking_over() -> StepResult:
    """The attempt of a turn parked on a takeover to sign in."""
    return StepResult(
        ending=StepEnding.ASKED,
        summary="The turn asks you to take over the browser.",
        ask=WaitingTakeover(
            turn_id=uuid4(),
            request=TakeoverRequested(takeover_id=uuid4(), reason="Sign in to Moodle."),
        ),
    )


def _hub(tmp_path: Path, control: FakeControl, runtime: FakeRuntime) -> Hub:
    write_factory(tmp_path / "hub" / "factories" / "tickets", KINBY)
    return hub_at(
        tmp_path / "hub",
        runtime=runtime,
        control=control,
        shipped_factories=tmp_path / "shipped",
        browser=FakeBrowserRuntime("ws://nowhere"),
    )


async def _taken_over(hub: Hub, runtime: FakeRuntime) -> tuple[UUID, UUID]:
    """A run on a browser workbench whose kinby step parked on a takeover: its owner and the
    run."""
    await _home(hub)
    line = await _line(hub_client(hub), runtime, BROWSING)
    run = await _handed_in(hub_client(hub), line, 12)
    await parked(hub, run.run_id)
    return line.owner_id, run.run_id


async def _finished(hub: Hub, run_id: UUID, ask: WaitingTakeover) -> object:
    command = FactoryRunTakeoverFinishCommand(run_id=run_id, takeover_id=ask.request.takeover_id)
    return await hub_client(hub).call(FACTORY_RUN_TAKEOVER_FINISH, command)


def test_a_kinby_steps_takeover_parks_its_run_and_raises_a_notification_naming_the_run(
    tmp_path,
):
    control = FakeControl()
    taking_over = _taking_over()
    control.step_results = [taking_over]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)

    async def scenario() -> None:
        owner, run_id = await _taken_over(hub, runtime)
        found = await detail(hub, run_id)
        [notification] = await _notifications(hub, owner)

        assert (found.run.status, found.run.step) == (FactoryRunStatus.PARKED, "implement")
        assert [(a.ending, a.ask) for a in found.attempts] == [(StepEnding.ASKED, taking_over.ask)]
        assert notification.title == "Takeover waiting"
        assert notification.text == (
            'The run for issue 12 asks you to take over its browser at "implement": '
            "Sign in to Moodle."
        )
        assert notification.options == []

    asyncio.run(scenario())


def test_finishing_the_takeover_resumes_the_turn_as_the_steps_next_attempt(tmp_path):
    control = FakeControl()
    taking_over = _taking_over()
    control.step_results = [taking_over]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)

    async def scenario() -> None:
        _, run_id = await _taken_over(hub, runtime)
        assert isinstance(taking_over.ask, WaitingTakeover)

        finished = await _finished(hub, run_id, taking_over.ask)
        done = await settled(hub, run_id)

        assert isinstance(finished, FactoryRun), finished
        assert done.run.status is FactoryRunStatus.DONE
        [(_, first), (_, resumed), _] = control.steps
        finish = StepAnswer(decision=ApprovalDecision.APPROVE)
        assert resumed.step == AgentStepRun(
            prompt="Implement the issue.\n",
            model="anthropic:claude-opus-5-5",
            answered=AnsweredAsk(ask=taking_over.ask, answer=finish),
        )
        assert resumed.folder == first.folder
        assert [(a.step, a.ending, a.answer) for a in done.attempts] == [
            ("implement", StepEnding.ASKED, finish),
            ("implement", StepEnding.CLEAN, None),
            ("fix", StepEnding.CLEAN, None),
        ]

    asyncio.run(scenario())


def test_a_takeover_the_run_is_not_parked_on_is_not_found(tmp_path):
    control = FakeControl()
    taking_over = _taking_over()
    control.step_results = [taking_over]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)

    async def scenario() -> None:
        _, run_id = await _taken_over(hub, runtime)
        assert isinstance(taking_over.ask, WaitingTakeover)
        other = taking_over.ask.model_copy(
            update={"request": TakeoverRequested(takeover_id=uuid4(), reason="Another.")}
        )

        refused = await _finished(hub, run_id, other)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.NOT_FOUND
        assert (await detail(hub, run_id)).run.status is FactoryRunStatus.PARKED

    asyncio.run(scenario())


def test_a_takeover_takes_no_answer_to_an_approval(tmp_path):
    control = FakeControl()
    taking_over = _taking_over()
    control.step_results = [taking_over]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)

    async def scenario() -> None:
        _, run_id = await _taken_over(hub, runtime)

        refused = await hub_client(hub).call(
            FACTORY_RUN_ANSWER,
            FactoryRunAnswerCommand(
                run_id=run_id,
                approval_id=uuid4(),
                answer=StepAnswer(decision=ApprovalDecision.APPROVE),
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())
