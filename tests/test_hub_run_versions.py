"""A factory run keeps the factory version it started on, through the hub contract."""

import asyncio
import sqlite3

from kinby.contracts import ClientStepRun, FactoryRunStatus, StepEnding, StepResult
from tests.test_factory_runs import settled
from tests.test_hub import FakeControl, FakeRuntime, hub_client
from tests.test_hub_factory_versions import edit
from tests.test_hub_line_agents import _hub
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
