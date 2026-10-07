"""Wait and approve steps: runs parked on a signal or on the user, holding no instance."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import aiohttp
import pytest
from aiohttp import web

from kinby.contracts import (
    FACTORY_RUN_APPROVE,
    FACTORY_RUN_SEND_BACK,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunApproveCommand,
    FactoryRunDetail,
    FactoryRunSendBackCommand,
    FactoryRunStatus,
    StepEnding,
)
from kinby.hub import Hub
from kinby.instance import Serve
from tests.test_factory_runs import (
    FILES,
    detail,
    factory_hub,
    handed_in,
    installed_coder,
    needing_human,
    run_events,
    settled,
)
from tests.test_hub import FakeControl, FakeImages, FakeRuntime, hub_client
from tests.test_hub_server import served, url

WAIT = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: babysit
    kind: wait
    signal:
      - routine: github
        headers.X-GitHub-Event: issue_comment
        body.issue.number: "{{issue}}"
    deadline: 7d
  - id: test
    kind: command
    in: coder
    run: ["make test"]
"""
WAIT_FILES = FILES | {"factory.yaml": WAIT}
COMMENT = b'{"action": "created", "issue": {"number": 7}}'


@asynccontextmanager
async def instance_answering(status: int, received: list[bytes]) -> AsyncIterator[Serve]:
    """Stand in for an instance's receiver that answers every signal with *status*."""

    async def receive(request: web.Request) -> web.Response:
        received.append(await request.read())
        return web.json_response({"thread_id": "recorded", "sequence": 7}, status=status)

    application = web.Application()
    application.router.add_post("/signals/{routine}", receive)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield Serve("127.0.0.1", site.port)
    finally:
        await runner.cleanup()


async def parked(hub: Hub, run_id: UUID) -> None:
    async with asyncio.timeout(5):
        while (await detail(hub, run_id)).run.status is not FactoryRunStatus.PARKED:
            await asyncio.sleep(0.01)


async def awaiting_approval(hub: Hub, run_id: UUID, attempt: int = 1) -> FactoryRunDetail:
    """The run once it parks at its approve step for the *attempt*-th time."""
    async with asyncio.timeout(5):
        while True:
            found = await detail(hub, run_id)
            last = found.attempts[-1] if found.attempts else None
            if found.run.status is FactoryRunStatus.PARKED and last and last.attempt == attempt:
                return found
            await asyncio.sleep(0.01)


async def stopped(hub: Hub, run_id: UUID, timeout: float = 5) -> FactoryRunDetail:
    """The run once it needs a human or is done."""
    async with asyncio.timeout(timeout):
        while True:
            found = await detail(hub, run_id)
            if found.run.status in {FactoryRunStatus.NEEDS_HUMAN, FactoryRunStatus.DONE}:
                return found
            await asyncio.sleep(0.01)


async def signalled(
    hub: Hub,
    runtime: FakeRuntime,
    coder: UUID,
    body: bytes,
    *,
    routine: str = "github",
    event: str = "issue_comment",
    answer: int = 202,
    headers: dict[str, str] | None = None,
) -> tuple[int, list[bytes]]:
    """Post one webhook to the coder's public signal path, and what its instance received."""
    received: list[bytes] = []
    async with served(hub) as address, instance_answering(answer, received) as private:
        runtime.addresses[str(coder)] = f"http://{private.host}:{private.port}"
        async with (
            aiohttp.ClientSession() as sender,
            sender.post(
                url(address, f"/instances/{coder}/signals/{routine}"),
                data=body,
                headers={"X-GitHub-Event": event} | (headers or {}),
            ) as answered,
        ):
            return answered.status, received


def test_a_signal_matching_a_parked_wait_moves_its_run_on(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, WAIT_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await parked(hub, run.run_id)
        waiting = await detail(hub, run.run_id)
        status, received = await signalled(hub, runtime, coder, COMMENT)
        finished = await settled(hub, run.run_id)

        assert (waiting.run.status, waiting.run.step) == (FactoryRunStatus.PARKED, "babysit")
        assert [(a.step, a.ending) for a in waiting.attempts] == [("babysit", None)]
        assert (status, received) == (202, [COMMENT])
        assert finished.run.status is FactoryRunStatus.DONE
        assert [(a.step, a.ending) for a in finished.attempts] == [
            ("babysit", StepEnding.CLEAN),
            ("test", StepEnding.CLEAN),
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("body", "routine", "event", "answer"),
    [
        (b'{"issue": {"number": 8}}', "github", "issue_comment", 202),
        (b'{"issue": {"number": "7"}}', "github", "issue_comment", 202),
        (b'{"issue": {}}', "github", "issue_comment", 202),
        (b'{"issue": 7}', "github", "issue_comment", 202),
        (b"[7]", "github", "issue_comment", 202),
        (b"issue 7", "github", "issue_comment", 202),
        (COMMENT, "github", "issues", 202),
        (COMMENT, "news", "issue_comment", 202),
        (COMMENT, "github", "issue_comment", 200),
        (COMMENT, "github", "issue_comment", 401),
    ],
)
def test_a_signal_that_does_not_match_or_the_instance_did_not_accept_leaves_the_run_parked(
    tmp_path, body, routine, event, answer
):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, WAIT_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await parked(hub, run.run_id)
        status, received = await signalled(
            hub, runtime, coder, body, routine=routine, event=event, answer=answer
        )
        await asyncio.sleep(0.05)
        found = await detail(hub, run.run_id)

        assert (status, received) == (answer, [body])
        assert (found.run.status, found.run.step) == (FactoryRunStatus.PARKED, "babysit")
        assert [(a.step, a.ending) for a in found.attempts] == [("babysit", None)]
        assert control.steps == []

    asyncio.run(scenario())


REVIEW_OR_CHECKS = WAIT.replace(
    """\
      - routine: github
        headers.X-GitHub-Event: issue_comment
        body.issue.number: "{{issue}}"
""",
    """\
      - headers.X-GitHub-Event: pull_request_review
        body.pull_request.number: "{{issue}}"
      - headers.X-GitHub-Event: check_suite
        body.check_suite.head_branch: agent/7
""",
)


@pytest.mark.parametrize(
    ("body", "event", "moves"),
    [
        (b'{"pull_request": {"number": 7}}', "pull_request_review", True),
        (b'{"check_suite": {"head_branch": "agent/7"}}', "check_suite", True),
        (b'{"check_suite": {"head_branch": "agent/7"}}', "pull_request_review", False),
        (b'{"pull_request": {"number": 7}}', "check_suite", False),
    ],
    ids=["first", "second", "first-header-second-body", "second-header-first-body"],
)
def test_a_wait_with_several_filters_moves_on_when_a_signal_matches_any_one_of_them(
    tmp_path, body, event, moves
):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(
        tmp_path / "hub", control, runtime, FILES | {"factory.yaml": REVIEW_OR_CHECKS}
    )

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await parked(hub, run.run_id)
        await signalled(hub, runtime, coder, body, event=event)
        await asyncio.sleep(0.05)
        found = await settled(hub, run.run_id)

        assert found.run.status is (FactoryRunStatus.DONE if moves else FactoryRunStatus.PARKED)

    asyncio.run(scenario())


def test_a_wait_never_matches_a_header_the_hub_does_not_forward_to_the_instance(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    on_cookie = WAIT.replace(
        "headers.X-GitHub-Event: issue_comment", "headers.Cookie: kinby_session=forged"
    )
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": on_cookie})

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await parked(hub, run.run_id)
        status, _ = await signalled(
            hub, runtime, coder, COMMENT, headers={"Cookie": "kinby_session=forged"}
        )
        await asyncio.sleep(0.05)
        found = await detail(hub, run.run_id)

        assert status == 202
        assert (found.run.status, found.run.step) == (FactoryRunStatus.PARKED, "babysit")

    asyncio.run(scenario())


@pytest.mark.parametrize(("retry", "attempts"), [("", 1), ("    retry: 1\n", 2)])
def test_a_wait_whose_deadline_passes_fails_under_its_retry_then_needs_a_human(
    tmp_path, retry, attempts
):
    control = FakeControl()
    runtime = FakeRuntime()
    factory = WAIT.replace("deadline: 7d\n", "deadline: 1s\n" + retry)
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await parked(hub, run.run_id)
        finished = await stopped(hub, run.run_id, timeout=attempts + 3)

        assert (finished.run.status, finished.run.step) == (
            FactoryRunStatus.NEEDS_HUMAN,
            "babysit",
        )
        assert [(a.step, a.ending) for a in finished.attempts] == [
            ("babysit", StepEnding.FAILED)
        ] * attempts
        assert finished.attempts[-1].summary == (
            'No signal matched step "babysit" within its 1s deadline.'
        )
        assert control.steps == []

    asyncio.run(scenario())


def test_a_parked_wait_stays_parked_through_a_hub_restart_and_keeps_its_deadline(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    factory = WAIT.replace("deadline: 7d", "deadline: 1s")
    hub = factory_hub(directory, control, runtime, FILES | {"factory.yaml": factory})

    async def before_the_restart() -> UUID:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await parked(hub, run.run_id)
        return run.run_id

    run_id = asyncio.run(before_the_restart())
    hub.close()
    restarted = Hub(
        directory,
        runtime=runtime,
        images=FakeImages(),
        control=control,
        shipped_factories=directory / "shipped",
    )

    async def after_the_restart() -> None:
        await restarted.recover()
        still = await detail(restarted, run_id)
        finished = await stopped(restarted, run_id)

        assert still.run.status is FactoryRunStatus.PARKED
        assert [(a.step, a.ending) for a in still.attempts] == [("babysit", None)]
        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert [(a.attempt, a.ending) for a in finished.attempts] == [(1, StepEnding.FAILED)]

    asyncio.run(after_the_restart())


APPROVE = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: test
    kind: command
    in: coder
    run: ["make test"]
  - id: merge
    kind: approve
    summary: Merge the branch.
  - id: publish
    kind: command
    in: coder
    run: ["make publish"]
"""
APPROVE_FILES = FILES | {"factory.yaml": APPROVE}


def test_approving_a_run_parked_at_an_approve_step_moves_it_on(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, APPROVE_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        waiting = await awaiting_approval(hub, run.run_id)
        needing = await needing_human(hub)
        async with run_events(hub) as events:
            approved = await hub_client(hub).call(
                FACTORY_RUN_APPROVE, FactoryRunApproveCommand(run_id=run.run_id)
            )
            finished = await settled(hub, run.run_id)

        assert (waiting.run.status, waiting.run.step) == (FactoryRunStatus.PARKED, "merge")
        assert needing == {coder: 1}
        assert isinstance(approved, FactoryRun)
        assert (approved.status, approved.step) == (FactoryRunStatus.QUEUED, "publish")
        assert events[0] == approved
        assert finished.run.status is FactoryRunStatus.DONE
        assert [(a.step, a.ending, a.summary) for a in finished.attempts] == [
            ("test", StepEnding.CLEAN, ""),
            ("merge", StepEnding.CLEAN, "The user approved it."),
            ("publish", StepEnding.CLEAN, ""),
        ]
        assert [command.origin.step for _, command in control.steps] == ["test", "publish"]
        assert await needing_human(hub) == {coder: 0}

    asyncio.run(scenario())


def test_sending_back_a_run_that_awaits_approval_runs_the_earlier_step_again(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, APPROVE_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await awaiting_approval(hub, run.run_id)
        sent = await hub_client(hub).call(
            FACTORY_RUN_SEND_BACK, FactoryRunSendBackCommand(run_id=run.run_id, step="test")
        )
        again = await awaiting_approval(hub, run.run_id, attempt=2)

        assert isinstance(sent, FactoryRun)
        assert (sent.status, sent.step) == (FactoryRunStatus.QUEUED, "test")
        assert [(a.step, a.attempt, a.ending, a.summary) for a in again.attempts] == [
            ("test", 1, StepEnding.CLEAN, ""),
            ("merge", 1, StepEnding.CLEAN, 'The user sent the run back to "test".'),
            ("test", 2, StepEnding.CLEAN, ""),
            ("merge", 2, None, ""),
        ]

    asyncio.run(scenario())


def test_only_a_run_parked_at_an_approve_step_is_approved_or_sent_back_from_there(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, WAIT_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await parked(hub, run.run_id)
        before = await detail(hub, run.run_id)
        approved = await hub_client(hub).call(
            FACTORY_RUN_APPROVE, FactoryRunApproveCommand(run_id=run.run_id)
        )
        sent = await hub_client(hub).call(
            FACTORY_RUN_SEND_BACK, FactoryRunSendBackCommand(run_id=run.run_id, step="babysit")
        )
        missing = await hub_client(hub).call(
            FACTORY_RUN_APPROVE, FactoryRunApproveCommand(run_id=UUID(int=0))
        )

        assert isinstance(approved, ErrorEnvelope)
        assert approved.code is ErrorCode.INVALID_ARGUMENT
        assert approved.message == (
            f'Factory run "{run.run_id}" is parked, so it awaits no approval.'
        )
        assert isinstance(sent, ErrorEnvelope)
        assert sent.code is ErrorCode.INVALID_ARGUMENT
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND
        assert await detail(hub, run.run_id) == before
        assert await needing_human(hub) == {coder: 0}

    asyncio.run(scenario())


def test_a_run_awaiting_approval_is_only_sent_back_to_an_earlier_step(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, APPROVE_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        waiting = await awaiting_approval(hub, run.run_id)
        refused = await hub_client(hub).call(
            FACTORY_RUN_SEND_BACK, FactoryRunSendBackCommand(run_id=run.run_id, step="publish")
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.message == 'Step "publish" is not a step before "merge".'
        assert await detail(hub, run.run_id) == waiting

    asyncio.run(scenario())


def test_a_parked_run_never_blocks_another_runs_step_on_its_instance(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, APPROVE_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        first = await handed_in(hub, coder, 7)
        await awaiting_approval(hub, first.run_id)
        second = await handed_in(hub, coder, 8)
        await awaiting_approval(hub, second.run_id)
        await hub_client(hub).call(
            FACTORY_RUN_APPROVE, FactoryRunApproveCommand(run_id=second.run_id)
        )
        finished = await settled(hub, second.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert (await detail(hub, first.run_id)).run.status is FactoryRunStatus.PARKED
        assert [
            (command.work_item["issue"], command.origin.step) for _, command in control.steps
        ] == [(7, "test"), (8, "test"), (8, "publish")]
        assert await needing_human(hub) == {coder: 1}

    asyncio.run(scenario())
