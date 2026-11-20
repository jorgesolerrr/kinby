"""A factory run keeps the factory version it started on, through the hub contract."""

import asyncio
import sqlite3

from kinby.contracts import (
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    ClientStepRun,
    FactoryResult,
    FactoryRun,
    FactoryRunRetryCommand,
    FactoryRunSendBackCommand,
    FactoryRunStatus,
    StepEnding,
    StepResult,
)
from tests.test_factory_runs import settled
from tests.test_hub import FakeControl, FakeRuntime, hub_client
from tests.test_hub_factory_step_off import set_off
from tests.test_hub_factory_versions import edit
from tests.test_hub_line_agents import AGENTS, _hub
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_lines import _home

EDITED = "Fix it another way.\n"


def _control() -> FakeControl:
    """A workbench whose implement step records the session the fix step resumes."""
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN, session="claude-1")]
    return control


async def _holding(control: FakeControl, count: int) -> None:
    """Wait until the workbench holds the run's *count*-th step."""
    async with asyncio.timeout(5):
        while len(control.steps) < count:
            await asyncio.sleep(0.01)


def _release(control: FakeControl) -> None:
    """Let the steps the workbench holds answer, and hold the ones after them."""
    control.step_release.set()
    control.step_release.clear()


def _prompts(control: FakeControl) -> list[str]:
    return [
        command.step.prompt
        for _, command in control.steps
        if isinstance(command.step, ClientStepRun)
    ]


def test_a_run_runs_its_next_step_on_the_version_it_started_on_after_an_edit(tmp_path):
    control = _control()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await _holding(control, 1)
        edited = await edit(client, {"prompts/fix.md": EDITED})
        _release(control)
        await _holding(control, 2)

        assert run.version is not None
        tree = tmp_path / "hub" / "factories" / ".versions" / "tickets" / run.version
        prompt = tree / "prompts" / "fix.md"
        assert prompt.read_text(encoding="utf-8") == "Fix what the review found.\n"
        assert prompt.stat().st_mode & 0o222 == 0

        control.step_release.set()
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert edited.version != run.version
        assert _prompts(control) == ["Implement the issue.\n", "Fix what the review found.\n"]
        assert [command.version for _, command in control.steps] == 2 * [run.version]
        assert done.run.version == run.version
        assert [attempt.version for attempt in done.attempts] == 2 * [run.version]
        assert not tree.exists()

    asyncio.run(scenario())


def test_a_run_starts_on_the_factorys_latest_version(tmp_path):
    control = _control()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        edited = await edit(client, {"prompts/fix.md": EDITED})

        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)

        assert run.version == edited.version
        assert _prompts(control)[-1] == EDITED

    asyncio.run(scenario())


def test_a_run_that_kept_no_version_reads_the_latest_one(tmp_path):
    control = _control()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await _holding(control, 1)
        # As a run started before runs kept their factory version.
        with sqlite3.connect(tmp_path / "hub" / "registry.sqlite") as connection:
            connection.execute("UPDATE factory_runs SET version = NULL")
            connection.execute("UPDATE step_attempts SET version = NULL")
        await edit(client, {"prompts/fix.md": EDITED})
        control.step_release.set()
        done = await settled(hub, run.run_id)

        assert _prompts(control) == ["Implement the issue.\n", EDITED]
        assert [command.version for _, command in control.steps] == [run.version, None]
        assert done.run.version is None
        assert [attempt.version for attempt in done.attempts] == [None, None]

    asyncio.run(scenario())


def test_a_versions_tree_stays_until_no_unfinished_run_keeps_it(tmp_path):
    control = _control()
    control.step_results *= 2
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        first = await _handed_in(client, line, 12)
        second = await _handed_in(client, line, 13)
        for held in range(1, 4):
            await _holding(control, held)
            _release(control)
        await _holding(control, 4)

        assert first.version is not None
        tree = tmp_path / "hub" / "factories" / ".versions" / "tickets" / first.version
        assert (await settled(hub, first.run_id)).run.status is FactoryRunStatus.DONE
        assert second.version == first.version
        assert tree.is_dir()

        control.step_release.set()
        await settled(hub, second.run_id)

        assert not tree.exists()

    asyncio.run(scenario())


def test_a_versions_tree_keeps_its_links_and_executable_files(tmp_path):
    control = _control()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)
    folder = tmp_path / "hub" / "factories" / "tickets"
    (folder / "prompts" / "shared.md").write_text("Fix what they shared.\n", encoding="utf-8")
    (folder / "prompts" / "fix.md").unlink()
    (folder / "prompts" / "fix.md").symlink_to("shared.md")
    (folder / "tools" / "helper.sh").parent.mkdir()
    (folder / "tools" / "helper.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (folder / "tools" / "helper.sh").chmod(0o755)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await _holding(control, 1)

        assert run.version is not None
        tree = tmp_path / "hub" / "factories" / ".versions" / "tickets" / run.version
        assert (tree / "tools" / "helper.sh").stat().st_mode & 0o777 == 0o555

        control.step_release.set()
        await settled(hub, run.run_id)

        assert _prompts(control) == ["Implement the issue.\n", "Fix what they shared.\n"]

    asyncio.run(scenario())


STUCK = StepResult(ending=StepEnding.NEEDS_HUMAN, summary="The tests will not pass.")
TEST_FIRST = "Implement it test-first.\n"


async def _retried(client, run: FactoryRun, latest: bool = False) -> None:
    command = FactoryRunRetryCommand(run_id=run.run_id, latest=latest)
    assert isinstance(await client.call(FACTORY_RUN_RETRY, command), FactoryRun)


async def _sent_back(client, run: FactoryRun, step: str, latest: bool = False) -> None:
    command = FactoryRunSendBackCommand(run_id=run.run_id, step=step, latest=latest)
    assert isinstance(await client.call(FACTORY_RUN_SEND_BACK, command), FactoryRun)


def test_a_retry_keeps_the_runs_version_and_one_that_moves_runs_the_latest(tmp_path):
    control = FakeControl()
    control.step_results = [STUCK, STUCK, StepResult(ending=StepEnding.CLEAN, session="claude-1")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        edited = await edit(client, {"prompts/implement.md": TEST_FIRST})

        await _retried(client, run)
        kept = await settled(hub, run.run_id)
        await _retried(client, run, latest=True)
        moved = await settled(hub, run.run_id)

        assert kept.run.version == run.version
        assert moved.run.status is FactoryRunStatus.DONE
        assert moved.run.version == edited.version
        assert _prompts(control) == [
            "Implement the issue.\n",
            "Implement the issue.\n",
            TEST_FIRST,
            "Fix what the review found.\n",
        ]
        assert [attempt.version for attempt in moved.attempts] == 2 * [run.version] + 2 * [
            edited.version
        ]

    asyncio.run(scenario())


def test_a_send_back_keeps_the_runs_version_and_one_that_moves_runs_the_latest(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN, session="claude-1"), STUCK]
    control.step_results += [StepResult(ending=StepEnding.CLEAN, session="claude-2"), STUCK]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        edited = await edit(client, {"prompts/implement.md": TEST_FIRST})

        await _sent_back(client, run, "implement")
        kept = await settled(hub, run.run_id)
        await _sent_back(client, run, "implement", latest=True)
        moved = await settled(hub, run.run_id)

        assert kept.run.version == run.version
        assert moved.run.version == edited.version
        assert _prompts(control)[::2] == [
            "Implement the issue.\n",
            "Implement the issue.\n",
            TEST_FIRST,
        ]

    asyncio.run(scenario())


THREE = AGENTS | {
    "factory.yaml": """\
name: tickets
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: implement
    kind: agent
    engine: claude
    prompt: prompts/implement.md
  - id: review
    kind: agent
    engine: claude
    prompt: prompts/review.md
  - id: fix
    kind: agent
    engine: claude
    prompt: prompts/fix.md
""",
    "prompts/review.md": "Review the change.\n",
}


def test_a_run_moved_onto_a_version_where_its_step_is_off_goes_on_to_the_next_step_on(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN), STUCK]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, THREE)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        turned = await set_off(client, "review")
        assert isinstance(turned, FactoryResult)

        await _retried(client, run, latest=True)
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert [attempt.step for attempt in done.attempts] == ["implement", "review", "fix"]
        assert done.attempts[-1].version == turned.version

    asyncio.run(scenario())
