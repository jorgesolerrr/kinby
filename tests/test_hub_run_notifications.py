"""A factory run that needs human: the hub notifies the user, whose reply moves the run on."""

import asyncio
from pathlib import Path

import pytest

from kinby.contracts import (
    FACTORY_RUN_RETRY,
    FactoryRun,
    FactoryRunRetryCommand,
    FactoryRunStatus,
    ReplyStatus,
    StepEnding,
    StepResult,
)
from kinby.hub import Hub
from tests.test_factory_runs import FACTORY, FILES, handed_in, settled, started_coder
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_channels import _PUBLIC_URL, FakeService, FakeTime, next_sent, paired_channel
from tests.test_hub_factories import write_factory
from tests.test_hub_replies import next_edit, replied, replies
from tests.test_hub_server import served

TWO_STEPS = FILES | {
    "factory.yaml": FACTORY.replace(
        "steps:\n",
        'steps:\n  - id: build\n    kind: command\n    in: coder\n    run: ["uv build"]\n',
    )
}


def run_hub(
    directory: Path,
    control: FakeControl,
    runtime: FakeRuntime,
    service: FakeService,
    files: dict[str, str] = FILES,
) -> Hub:
    write_factory(directory / "factories" / "checks", files)
    time = FakeTime()
    return hub_at(
        directory,
        runtime=runtime,
        control=control,
        shipped_factories=directory / "shipped",
        private_url="http://hub:8080",
        channel_kinds={"fake": service.kind()},
        clock=time.clock,
        sleep=time.sleep,
        public_url=_PUBLIC_URL,
    )


async def moved_on(hub: Hub, run: FactoryRun, status: FactoryRunStatus) -> FactoryRun:
    async with asyncio.timeout(5):
        while (found := await settled(hub, run.run_id)).run.status is not status:
            await asyncio.sleep(0.01)
    return found.run


@pytest.mark.parametrize(
    ("option", "outcome", "status", "steps"),
    [
        (0, "Retried.", FactoryRunStatus.DONE, 2),
        (1, "Cancelled.", FactoryRunStatus.CANCELLED, 1),
    ],
    ids=["retry", "cancel"],
)
def test_a_needs_human_stop_raises_a_notification_whose_reply_moves_the_run(
    tmp_path, option, outcome, status, steps
):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED, summary="Tests failed.")]
    runtime = FakeRuntime()
    service = FakeService()
    hub = run_hub(tmp_path / "hub", control, runtime, service)

    async def scenario() -> None:
        async with served(hub) as address:
            channel = await paired_channel(hub_client(hub), address)
            coder = await started_coder(hub, runtime)
            run = await handed_in(hub, coder, 7)
            stopped = await settled(hub, run.run_id)
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, option)
            edited = await next_edit(service)
            moved = await moved_on(hub, run, status)

        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        notification = sent.notification
        assert notification.header == "checks-coder · checks"
        assert notification.title == "Needs human"
        assert notification.text == 'The run for issue 7 stopped at "test".'
        assert notification.link == f"{_PUBLIC_URL}/library"
        assert notification.options == ("Retry", "Cancel", "Mute this instance")
        assert edited == (sent.ref, outcome)
        assert moved.status is status
        assert len(control.steps) == steps
        [reply] = await replies(hub, coder)
        assert (reply.status, reply.outcome) == (ReplyStatus.APPLIED, outcome)

    asyncio.run(scenario())


def test_a_send_back_reply_sends_the_run_back_to_the_step_before_its_stop(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN),
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
    ]
    runtime = FakeRuntime()
    service = FakeService()
    hub = run_hub(tmp_path / "hub", control, runtime, service, TWO_STEPS)

    async def scenario() -> None:
        async with served(hub) as address:
            channel = await paired_channel(hub_client(hub), address)
            coder = await started_coder(hub, runtime)
            run = await handed_in(hub, coder, 7)
            await settled(hub, run.run_id)
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 1)
            edited = await next_edit(service)
            await moved_on(hub, run, FactoryRunStatus.DONE)

        assert sent.notification.options == (
            "Retry",
            "Send back to build",
            "Cancel",
            "Mute this instance",
        )
        assert edited == (sent.ref, 'Sent back to "build".')
        assert [command.origin.step for _, command in control.steps] == [
            "build",
            "test",
            "build",
            "test",
        ]

    asyncio.run(scenario())


def test_a_reply_to_an_earlier_stop_of_a_run_is_stale_and_changes_nothing(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
        StepResult(ending=StepEnding.FAILED, summary="Tests failed again."),
    ]
    runtime = FakeRuntime()
    service = FakeService()
    hub = run_hub(tmp_path / "hub", control, runtime, service)

    async def scenario() -> None:
        client = hub_client(hub)
        async with served(hub) as address:
            channel = await paired_channel(client, address)
            coder = await started_coder(hub, runtime)
            run = await handed_in(hub, coder, 7)
            await settled(hub, run.run_id)
            first = await next_sent(service)
            retried = await client.call(
                FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=run.run_id)
            )
            assert isinstance(retried, FactoryRun), retried
            second = await next_sent(service)
            await replied(address, channel, first.ref, 0)
            edited = await next_edit(service)

        assert first.notification.title == second.notification.title == "Needs human"
        assert edited == (first.ref, "The run already moved.")
        assert (await settled(hub, run.run_id)).run.status is FactoryRunStatus.NEEDS_HUMAN
        assert len(control.steps) == 2
        [reply] = await replies(hub, coder)
        assert reply.status is ReplyStatus.STALE

    asyncio.run(scenario())
