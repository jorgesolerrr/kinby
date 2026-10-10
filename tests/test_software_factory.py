"""The software factory kinby ships: its file on the hub, and its coder's tools and hooks."""

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_CHECK,
    FACTORY_EDIT,
    FACTORY_GET,
    FACTORY_INSTALL,
    FACTORY_RUN_INTAKE,
    FACTORY_RUN_LIST,
    INSTANCE_START,
    AcceptedResult,
    ClientStepRun,
    CodeStepRun,
    CodingClient,
    CommandStepRun,
    Event,
    FactoryCheckCommand,
    FactoryCheckResult,
    FactoryEditCommand,
    FactoryGetCommand,
    FactoryInstallCommand,
    FactoryInstallResult,
    FactoryInstanceSetup,
    FactoryResult,
    FactoryRun,
    FactoryRunDetail,
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunOrigin,
    FactoryRunStatus,
    InstanceStartCommand,
    LifecycleOperationResult,
    OperationState,
    ReasoningEffort,
    Scope,
    StepEnding,
    StepResult,
    StepRunCommand,
    StepValue,
    ToolResult,
    TurnCompleted,
    is_turn_closing,
)
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.factories import SHIPPED_FACTORIES
from kinby.hub import Hub
from kinby.instance import Instance, inspect_instance, load_instance
from tests.helpers import thread_events
from tests.test_drain import call
from tests.test_factory_runs import detail, intake_client, settled
from tests.test_factory_waits import signalled
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_client,
    instance_environment,
)
from tests.test_hub_server import served, url
from tests.test_routines import RoutineModel, signal_runtime
from tests.test_steps import CLAUDE_STREAM, streaming, stub_client

#: What the coder asks for at install: the repository, GitHub, Claude Code and a commit identity.
CODER_SETUP = FactoryInstanceSetup(
    model="anthropic:claude-sonnet-5",
    config={"repository": "https://github.com/owner/project.git"},
    secrets={
        "api_key": "sk-ant-test",
        "GH_TOKEN": "ghp-test",
        "GITHUB_WEBHOOK_SECRET": "webhook-secret",
        "CLAUDE_CODE_OAUTH_TOKEN": "claude-token",
        "GIT_USER_NAME": "Software factory",
        "GIT_USER_EMAIL": "factory@example.com",
    },
)


def fresh_hub(
    directory: Path, control: FakeControl | None = None, runtime: FakeRuntime | None = None
) -> Hub:
    """A hub that serves only the factories kinby ships."""
    return Hub(
        directory,
        runtime=runtime or FakeRuntime(),
        images=FakeImages(),
        control=control or FakeControl(),
        private_url="http://hub:8080",
    )


async def installed_coder(client: ContractClient) -> UUID:
    accepted = await client.call(
        FACTORY_INSTALL, FactoryInstallCommand(name="software", instances={"coder": CODER_SETUP})
    )
    assert isinstance(accepted, FactoryInstallResult), accepted
    [creation] = accepted.instances.values()
    assert (await finished_operation(client, creation)).state is OperationState.SUCCEEDED
    return creation.instance_id


def test_the_software_factory_passes_the_factory_check_and_installs_on_a_fresh_hub(tmp_path):
    hub = fresh_hub(tmp_path / "hub")
    client = hub_client(hub)

    async def scenario() -> None:
        checked = await client.call(FACTORY_CHECK, FactoryCheckCommand(name="software"))
        coder = await installed_coder(client)

        assert isinstance(checked, FactoryCheckResult)
        assert checked.problems == []
        instance = inspect_instance(hub.instances_directory / str(coder))
        assert instance.manifest.workspace.source == "https://github.com/owner/project.git"

    asyncio.run(scenario())


async def started_coder(hub: Hub, runtime: FakeRuntime) -> UUID:
    """Install the software factory and start its coder, which answers on its control endpoint."""
    client = hub_client(hub)
    coder = await installed_coder(client)
    started = await client.call(INSTANCE_START, InstanceStartCommand(instance_id=coder))
    assert isinstance(started, LifecycleOperationResult)
    assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
    runtime.addresses[str(coder)] = f"http://kinby-{coder}:8787"
    return coder


async def handed_in(hub: Hub, coder: UUID, issue: int) -> FactoryRun:
    run = await intake_client(hub, coder).call(
        FACTORY_RUN_INTAKE, FactoryRunIntakeCommand(routine="scan", work_item={"issue": issue})
    )
    assert isinstance(run, FactoryRun), run
    return run


#: What each step of a run that goes well hands back, in the order the steps run.
PREPARED = StepResult(
    ending=StepEnding.CLEAN, values={"branch": "agent/7-add-dark-mode", "base": "main"}
)
IMPLEMENTED = StepResult(ending=StepEnding.CLEAN, summary="Added dark mode.", session="claude-1")
#: The answer step before the pull request opens, with no review threads to answer yet.
NOTHING_TO_ANSWER = StepResult(ending=StepEnding.CLEAN, summary="No review threads yet.")
CHECKED = StepResult(ending=StepEnding.CLEAN, summary="Every command exited with code 0.")
SET_UP = CHECKED
#: The fix step when no check failed: it changes nothing.
NOTHING_TO_FIX = StepResult(ending=StepEnding.CLEAN, summary="Nothing failed.", session="claude-1")
#: Every step up to the pull request, the checks passing at the first try.
CHECKED_CHANGE = [PREPARED, SET_UP, IMPLEMENTED, NOTHING_TO_ANSWER, NOTHING_TO_FIX, CHECKED]
OPENED = StepResult(ending=StepEnding.CLEAN, values={"pr": 42})
#: A fix round: the trusted review threads on the pull request, then their answers, pushed.
THREADS = json.dumps(
    [
        {
            "id": "T1",
            "path": "theme.py",
            "line": 1,
            "comments": [{"author": "owner", "body": "Name it DARK_MODE."}],
        }
    ]
)
FEEDBACK = StepResult(
    ending=StepEnding.CLEAN, outcome="feedback", values={"feedback": THREADS, "pr": 42}
)
REPLIES = json.dumps({"T1": {"fixed": True, "reply": "Renamed it."}})
ANSWERED = StepResult(ending=StepEnding.CLEAN, summary="Renamed it.", values={"replies": REPLIES})
REPLIED = StepResult(ending=StepEnding.CLEAN, summary="Replied on 1 review thread.")
FIX_ROUND = [FEEDBACK, ANSWERED, NOTHING_TO_FIX, CHECKED, REPLIED]
WAITING = StepResult(ending=StepEnding.CLEAN, outcome="waiting")
MERGE_READY = StepResult(
    ending=StepEnding.CLEAN, outcome="merge-ready", values={"merge_ready": True}
)
#: The GitHub deliveries that wake a babysitting run: a review, and a check suite that finished.
REVIEWED = json.dumps({"action": "submitted", "pull_request": {"number": 42}}).encode()
CHECKS_DONE = json.dumps(
    {"action": "completed", "check_suite": {"head_branch": "agent/7-add-dark-mode"}}
).encode()


async def babysitting(hub: Hub, run_id: UUID, times: int = 1) -> FactoryRunDetail:
    """The run once it parks at its babysit wait for the *times*-th time."""
    async with asyncio.timeout(5):
        while True:
            found = await detail(hub, run_id)
            waits = [attempt for attempt in found.attempts if attempt.step == "babysit"]
            if found.run.status is FactoryRunStatus.PARKED and len(waits) == times:
                return found
            await asyncio.sleep(0.01)


async def woken(hub: Hub, runtime: FakeRuntime, coder: UUID, body: bytes, event: str) -> None:
    """Deliver a GitHub webhook to the coder's scan routine, which accepts it."""
    status, _ = await signalled(hub, runtime, coder, body, routine="scan", event=event)
    assert status == 202


def coding(step: str, minutes: int, resume: str | None = None) -> ClientStepRun:
    """What the hub asks the coder to run for one of the factory's client steps."""
    return ClientStepRun(
        client=CodingClient.CLAUDE,
        prompt=(SHIPPED_FACTORIES / "software" / "prompts" / f"{step}.md").read_text(),
        resume=resume,
        timeout_seconds=minutes * 60,
        model="claude-opus-5-5",
        effort=ReasoningEffort.HIGH,
    )


def test_the_software_factory_carries_an_issue_from_intake_to_a_pull_request_it_babysits(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [*CHECKED_CHANGE, OPENED]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        waiting = await babysitting(hub, run.run_id)

        assert [(attempt.step, attempt.ending) for attempt in waiting.attempts] == [
            ("prepare", StepEnding.CLEAN),
            ("setup", StepEnding.CLEAN),
            ("implement", StepEnding.CLEAN),
            ("answer", StepEnding.CLEAN),
            ("fix", StepEnding.CLEAN),
            ("checks", StepEnding.CLEAN),
            ("publish", StepEnding.CLEAN),
            ("babysit", None),
        ]
        prepare, setup, implement, answer, fix, checks, publish = (
            command for _, command in control.steps
        )
        assert prepare.step == CodeStepRun(call="prepare_branch")
        installs = [
            "git switch agent/7-add-dark-mode",
            "uv sync --frozen",
            "bun install --frozen-lockfile",
        ]
        assert setup.step == CommandStepRun(run=installs)
        assert implement.step == coding("implement", 60)
        assert implement.hook == "check_implementation"
        assert implement.results == PREPARED.values
        assert answer.step == coding("answer", 60, resume="claude-1")
        assert answer.hook == "check_answers"
        assert fix.step == coding("fix", 30, resume="claude-1")
        assert fix.hook == "check_implementation"
        assert checks.step == CommandStepRun(run=[*installs, "bun run check"])
        assert publish.step == CodeStepRun(call="publish_pull_request")
        assert publish.work_item == {"issue": 7}
        assert publish.results == PREPARED.values
        assert waiting.attempts[-2].values == {"pr": 42}

    asyncio.run(scenario())


def test_a_review_wakes_the_run_for_a_fix_round_and_it_finishes_once_merge_ready(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [
        *CHECKED_CHANGE,
        OPENED,
        *FIX_ROUND,
        MERGE_READY,
    ]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, coder, REVIEWED, "pull_request_review")
        await babysitting(hub, run.run_id, times=2)
        await woken(hub, runtime, coder, CHECKS_DONE, "check_suite")
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [attempt.step for attempt in finished.attempts] == [
            "prepare",
            "setup",
            "implement",
            "answer",
            "fix",
            "checks",
            "publish",
            "babysit",
            "assess",
            "answer",
            "fix",
            "checks",
            "publish",
            "babysit",
            "assess",
        ]
        asked = {}
        for _, command in control.steps:
            asked.setdefault(command.origin.step, []).append(command)
        assert [command.step for command in asked["assess"]] == [
            CodeStepRun(call="assess_pull_request")
        ] * 2
        assert asked["assess"][0].results["pr"] == 42
        assert asked["answer"][1].results["feedback"] == THREADS
        assert asked["publish"][1].results["replies"] == REPLIES
        assert asked["publish"][1].results["pr"] == 42
        assert finished.attempts[-1].values == {"merge_ready": True}

    asyncio.run(scenario())


def test_a_check_repair_in_a_fix_round_keeps_its_replies_and_the_pull_request(tmp_path):
    control = FakeControl()
    failure = '"bun run check" exited with code 1.\nruff: F401 `os` imported but unused'
    control.step_results = [
        *CHECKED_CHANGE,
        OPENED,
        FEEDBACK,
        ANSWERED,
        NOTHING_TO_FIX,
        StepResult(ending=StepEnding.FAILED, summary=failure),
        StepResult(ending=StepEnding.CLEAN, summary="Removed the import.", session="claude-1"),
        CHECKED,
        REPLIED,
    ]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, coder, REVIEWED, "pull_request_review")
        waiting = await babysitting(hub, run.run_id, times=2)

        assert [attempt.step for attempt in waiting.attempts][-7:] == [
            "answer",
            "fix",
            "checks",
            "fix",
            "checks",
            "publish",
            "babysit",
        ]
        fixes = [command for _, command in control.steps if command.origin.step == "fix"]
        assert fixes[-1].step == coding("fix", 30, resume="claude-1")
        assert fixes[-1].results["failure"] == failure
        _, publish = control.steps[-1]
        assert publish.step == CodeStepRun(call="publish_pull_request")
        assert (publish.results["replies"], publish.results["pr"]) == (REPLIES, 42)

    asyncio.run(scenario())


def test_the_run_finishes_only_when_the_pull_request_is_merge_ready(tmp_path):
    control = FakeControl()
    unrecorded = StepResult(ending=StepEnding.CLEAN, outcome="merge-ready")
    control.step_results = [
        *CHECKED_CHANGE,
        OPENED,
        WAITING,
        unrecorded,
    ]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, coder, CHECKS_DONE, "check_suite")
        again = await babysitting(hub, run.run_id, times=2)
        await woken(hub, runtime, coder, REVIEWED, "pull_request_review")
        stopped = await settled(hub, run.run_id)

        assert [attempt.step for attempt in again.attempts][-3:] == ["babysit", "assess", "babysit"]
        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "assess")
        assert stopped.attempts[-1].summary == (
            'The run\'s done_requires needs "merge_ready", which no step recorded.'
        )

    asyncio.run(scenario())


def test_a_reply_on_a_review_thread_wakes_the_babysitting_run(tmp_path):
    control = FakeControl()
    control.step_results = [*CHECKED_CHANGE, OPENED, MERGE_READY]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)
    replied = json.dumps({"action": "created", "pull_request": {"number": 42}}).encode()

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, coder, replied, "pull_request_review_comment")
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [attempt.step for attempt in finished.attempts][-2:] == ["babysit", "assess"]

    asyncio.run(scenario())


def test_the_round_limit_stops_the_run_as_needs_human_and_reports_it_on_the_issue(tmp_path):
    control = FakeControl()
    control.step_results = [
        *CHECKED_CHANGE,
        OPENED,
        *FIX_ROUND * 3,
        FEEDBACK,
    ]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        for round_number in range(1, 4):
            await babysitting(hub, run.run_id, times=round_number)
            await woken(hub, runtime, coder, REVIEWED, "pull_request_review")
        await babysitting(hub, run.run_id, times=4)
        await woken(hub, runtime, coder, REVIEWED, "pull_request_review")
        stopped = await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            # Seven steps to the pull request, three fix rounds of five, the last assess, and
            # the needs_human call.
            while len(control.steps) < 7 + 3 * 5 + 1 + 1:
                await asyncio.sleep(0.01)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "assess")
        assert [attempt.step for attempt in stopped.attempts].count("answer") == 4
        assert stopped.attempts[-1].summary == (
            'Step "assess" sent the work back to "answer" 3 times, its max.'
        )
        _, report = control.steps[-1]
        assert report.step == CodeStepRun(
            call="report_needs_human", summary=stopped.attempts[-1].summary
        )

    asyncio.run(scenario())


def test_opening_the_pull_request_is_never_retried_and_needs_human_reports_on_the_issue(
    tmp_path,
):
    control = FakeControl()
    refused = StepResult(
        ending=StepEnding.FAILED,
        summary='Tool "publish_pull_request" failed: gh exited with code 1: GitHub is down.',
    )
    control.step_results = [*CHECKED_CHANGE, refused]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        stopped = await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            while len(control.steps) < 8:
                await asyncio.sleep(0.01)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "publish")
        assert [attempt.step for attempt in stopped.attempts].count("publish") == 1
        _, report = control.steps[-1]
        assert report.step == CodeStepRun(call="report_needs_human", summary=refused.summary)
        assert (report.origin.run_id, report.origin.step) == (run.run_id, "publish")
        assert report.work_item == {"issue": 7}
        assert report.hook is None

    asyncio.run(scenario())


class FakeGitHub:
    """A gh first on PATH that records each call and answers from what the test told it."""

    def __init__(self, directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._answers = directory / "answers.json"
        self._calls = directory / "calls.jsonl"
        executable = directory / "bin" / "gh"
        executable.parent.mkdir(parents=True)
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            "arguments = sys.argv[1:]\n"
            f"with open({str(self._calls)!r}, 'a') as calls:\n"
            "    calls.write(json.dumps(arguments) + '\\n')\n"
            f"answers = Path({str(self._answers)!r})\n"
            "for needles, output, error in json.loads(answers.read_text()):\n"
            "    if all(needle in arguments for needle in needles):\n"
            "        if error is not None:\n"
            "            sys.exit(error)\n"
            "        print(output)\n"
            "        break\n"
        )
        executable.chmod(0o755)
        self._answers.write_text("[]")
        monkeypatch.setenv(
            "PATH", f"{executable.parent}:{Path(sys.executable).parent}:/usr/bin:/bin"
        )

    def answer(self, *needles: str, output: object = "", error: str | None = None) -> None:
        """Answer a call whose arguments hold every one of *needles*, the first answer that fits.

        An *error* fails the call with that message.
        """
        answers = json.loads(self._answers.read_text())
        text = output if isinstance(output, str) else json.dumps(output)
        answers.append([list(needles), text, error])
        self._answers.write_text(json.dumps(answers))

    @property
    def calls(self) -> list[list[str]]:
        if not self._calls.exists():
            return []
        return [json.loads(line) for line in self._calls.read_text().splitlines()]


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=directory, check=True, capture_output=True, text=True
    ).stdout.strip()


def coder_at(tmp_path: Path) -> Instance:
    """The software factory's coder as a fresh hub installs it, its workspace a clone of origin.

    origin's main holds one commit.
    """
    hub = fresh_hub(tmp_path / "hub")
    coder = asyncio.run(installed_coder(hub_client(hub)))
    hub.close()
    instance = load_instance(hub.instances_directory / str(coder))
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(("git", "init", "--bare", "-b", "main", str(origin)), check=True)
    subprocess.run(("git", "clone", str(origin), str(seed)), check=True, capture_output=True)
    commit(seed, "README.md", "The project.\n")
    git(seed, "push", "origin", "main")
    workspace = instance.manifest.workspace.path
    subprocess.run(("git", "clone", str(origin), str(workspace)), check=True, capture_output=True)
    git(workspace, "config", "user.name", "Software factory")
    git(workspace, "config", "user.email", "factory@example.com")
    return instance


def commit(directory: Path, name: str, content: str) -> None:
    (directory / name).write_text(content)
    git(directory, "add", name)
    git(
        directory,
        "-c",
        "user.name=Someone",
        "-c",
        "user.email=someone@example.com",
        "commit",
        "-m",
        f"Write {name}",
    )


async def run_in(dispatcher: ScheduledDispatcher, command: StepRunCommand) -> StepResult:
    """Run the step through the instance's dispatcher, as the hub's step.run does."""
    result = await call(dispatcher, "step.run", **command.model_dump(mode="json"))
    assert isinstance(result, StepResult), result
    return result


def origin(step: str) -> FactoryRunOrigin:
    return FactoryRunOrigin(factory="software", run_id=uuid4(), step=step)


BRANCH = {"branch": "agent/7-add-dark-mode", "base": "main"}
#: What a pull request's body ends with when no adversarial review ran before it opened.
REVIEW_NOT_RUN = (
    "\n\n## Review status\n\nAdversarial review was not run. Review happens on this pull request."
)


def test_needs_human_labels_the_issue_with_the_failing_steps_summary_and_keeps_the_branch(
    tmp_path, monkeypatch
):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    git(workspace, "switch", "-c", BRANCH["branch"])
    commit(workspace, "theme.py", "DARK = True\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    dispatcher, _ = signal_runtime(instance, RoutineModel())
    summary = 'Hook "check_implementation" failed: UncommittedWork: theme.py is not committed.'

    async def scenario() -> None:
        result = await run_in(
            dispatcher,
            StepRunCommand(
                step=CodeStepRun(call="report_needs_human", summary=summary),
                origin=origin("implement"),
                work_item={"issue": 7},
                results=BRANCH,
            ),
        )

        assert result.ending is StepEnding.CLEAN, result.summary
        assert [
            "issue",
            "edit",
            "7",
            "--remove-label",
            "ready-for-agent",
            "--add-label",
            "ready-for-human",
        ] in github.calls
        [comment] = [
            arguments for arguments in github.calls if arguments[:2] == ["issue", "comment"]
        ]
        assert comment[2:4] == ["7", "--body"]
        assert "implement" in comment[4]
        assert summary in comment[4]
        assert git(workspace, "branch", "--show-current") == BRANCH["branch"]
        assert git(workspace, "log", "-1", "--format=%s") == "Write theme.py"

    asyncio.run(scenario())


#: What the stub client runs to commit its work on the branch it is on.
COMMITS = (
    "import subprocess\n"
    "open('theme.py', 'w').write('DARK = True\\n')\n"
    "subprocess.run(['git', 'add', 'theme.py'], check=True, capture_output=True)\n"
    "subprocess.run(['git', 'commit', '-m', 'Add dark mode'], check=True, capture_output=True)\n"
)
LEAVES_CHANGES = "open('notes.md', 'w').write('Half done.\\n')\n"
MALFORMED = 'print(\'{"type": "result", "subt\')\n'


@pytest.mark.parametrize(
    ("then", "ending", "reason"),
    [
        (COMMITS + streaming(CLAUDE_STREAM), StepEnding.CLEAN, None),
        (COMMITS + MALFORMED, StepEnding.CLEAN, None),
        (
            streaming(CLAUDE_STREAM),
            StepEnding.FAILED,
            "Branch agent/7-add-dark-mode has no commits ahead of main.",
        ),
        (
            COMMITS + LEAVES_CHANGES + streaming(CLAUDE_STREAM),
            StepEnding.FAILED,
            "The workspace has changes no commit on agent/7-add-dark-mode holds:\n?? notes.md",
        ),
    ],
    ids=["committed", "committed-malformed-stream", "no-commits", "dirty-tree"],
)
def test_the_implement_hook_passes_only_work_committed_on_the_branch_however_the_client_spoke(
    tmp_path, monkeypatch, then, ending, reason
):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    git(workspace, "switch", "-c", BRANCH["branch"], "origin/main")
    stub_client(tmp_path, monkeypatch, "claude", then)
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await run_in(
            dispatcher,
            StepRunCommand(
                step=ClientStepRun(
                    client=CodingClient.CLAUDE, prompt="Implement it.", timeout_seconds=30
                ),
                hook="check_implementation",
                origin=origin("implement"),
                work_item={"issue": 7},
                results=BRANCH,
            ),
        )

        assert result.ending is ending, result.summary
        if reason is not None:
            assert result.summary.endswith(
                f'Hook "check_implementation" failed: UncommittedWork: {reason}'
            )

    asyncio.run(scenario())


ISSUE = "repos/{{owner}}/{{repo}}/issues/{number}"
REPOSITORY = {
    "name": "project",
    "owner": {"login": "owner"},
    "isInOrganization": False,
    "defaultBranchRef": {"name": "main"},
}


def issue(
    number: int,
    title: str,
    *,
    parent: int | None = None,
    author: str = "owner",
    association: str = "OWNER",
    labels: Sequence[str] = ("ready-for-agent",),
) -> dict[str, object]:
    """An open issue labeled ready-for-agent, as GitHub's REST API answers with it."""
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/owner/project/issues/{number}",
        "state": "open",
        "labels": [{"name": label} for label in labels],
        "user": {"login": author},
        "author_association": association,
        "parent_issue_url": (
            None
            if parent is None
            else f"https://api.github.com/repos/owner/project/issues/{parent}"
        ),
    }


def comment(author: str, association: str) -> dict[str, object]:
    return {"body": "A note.", "user": {"login": author}, "author_association": association}


def pull_request(
    number: int,
    branch: str,
    closes: int,
    stack: int | None = None,
    *,
    head_repository: str | None = "owner/project",
) -> dict[str, object]:
    """An open agent pull request, as GitHub's REST API lists it.

    A fork's has another *head_repository*, and one whose fork is gone has None.
    """
    return {
        "number": number,
        "html_url": f"https://github.com/owner/project/pull/{number}",
        "head": {
            "ref": branch,
            "sha": "abc123",
            "repo": None if head_repository is None else {"full_name": head_repository},
        },
        "base": {"repo": {"full_name": "owner/project"}},
        "body": f"Closes #{closes}\n\nWhat changed.",
        "stack": None if stack is None else {"number": stack},
    }


def answer_issue(
    github: FakeGitHub, found: dict[str, object], comments: Sequence[dict[str, object]] = ()
) -> None:
    number = found["number"]
    github.answer(ISSUE.format(number=number) + "/comments", output=[list(comments)])
    github.answer(ISSUE.format(number=number), output=found)


async def prepared(dispatcher: ScheduledDispatcher, number: int) -> StepResult:
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="prepare_branch"),
            origin=origin("prepare"),
            work_item={"issue": number},
        ),
    )


def test_prepare_checks_out_a_fresh_agent_branch_from_the_default_branch(tmp_path, monkeypatch):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    (workspace / "README.md").write_text("Left over from an earlier run.\n")
    (workspace / ".scratch").mkdir()
    (workspace / ".scratch" / "pr-body.md").write_text("An earlier run's body.\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode!"), [comment("helper", "COLLABORATOR")])
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await prepared(dispatcher, 7)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"branch": "agent/7-add-dark-mode", "base": "main"}
        assert git(workspace, "branch", "--show-current") == "agent/7-add-dark-mode"
        assert git(workspace, "rev-parse", "HEAD") == git(workspace, "rev-parse", "origin/main")
        assert git(workspace, "status", "--porcelain", "--untracked-files=all") == ""
        assert not (workspace / ".scratch").exists()
        (workspace / ".scratch").mkdir()
        (workspace / ".scratch" / "pr-body.md").write_text("This run's body.\n")
        assert git(workspace, "status", "--porcelain", "--untracked-files=all") == ""

    asyncio.run(scenario())


@pytest.mark.parametrize("written_by", ["issue", "comment", "parent"])
def test_prepare_fails_on_ticket_text_an_untrusted_author_wrote_before_any_branch(
    tmp_path, monkeypatch, written_by
):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    trusted = comment("helper", "MEMBER")
    stranger = comment("stranger", "NONE")
    match written_by:
        case "issue":
            answer_issue(github, issue(7, "Add dark mode", author="stranger", association="NONE"))
        case "comment":
            answer_issue(github, issue(7, "Add dark mode"), [trusted, stranger])
        case "parent":
            answer_issue(github, issue(7, "Add dark mode", parent=5), [trusted])
            answer_issue(github, issue(5, "Themes", author="stranger", association="CONTRIBUTOR"))
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await prepared(dispatcher, 7)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "prepare_branch" failed: UntrustedTicket: '
            "Ticket text from untrusted authors needs a human: stranger."
        )
        assert git(workspace, "branch", "--show-current") == "main"

    asyncio.run(scenario())


def test_prepare_stacks_a_sub_issue_on_its_newest_siblings_agent_branch(tmp_path, monkeypatch):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    git(workspace, "switch", "-c", "agent/7-light-theme")
    commit(workspace, "light.py", "LIGHT = True\n")
    git(workspace, "push", "origin", "agent/7-light-theme")
    git(workspace, "switch", "main")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(8, "Dark theme", parent=5))
    answer_issue(github, issue(5, "Themes"))
    github.answer(
        "repos/{owner}/{repo}/issues",
        output=[[issue(7, "Light theme", parent=5), issue(8, "Dark theme", parent=5)]],
    )
    github.answer(
        "repos/{owner}/{repo}/pulls",
        output=[[pull_request(41, "agent/7-light-theme", closes=7), pull_request(40, "fix", 3)]],
    )
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await prepared(dispatcher, 8)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"branch": "agent/8-dark-theme", "base": "agent/7-light-theme"}
        assert git(workspace, "log", "-1", "--format=%s") == "Write light.py"

    asyncio.run(scenario())


def implemented(workspace: Path, branch: str, body: str | None) -> None:
    """The workspace as the implement step leaves it: a commit on the branch, and the body the
    client wrote for its pull request."""
    git(workspace, "switch", "-c", branch, "origin/main")
    commit(workspace, "theme.py", "DARK = True\n")
    if body is not None:
        (workspace / ".scratch").mkdir()
        (workspace / ".scratch" / "pr-body.md").write_text(body)


async def opened(
    dispatcher: ScheduledDispatcher, number: int, results: Mapping[str, StepValue]
) -> StepResult:
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="publish_pull_request"),
            origin=origin("publish"),
            work_item={"issue": number},
            results=dict(results),
        ),
    )


def created_pull_request(github: FakeGitHub) -> list[str]:
    [created] = [arguments for arguments in github.calls if arguments[:2] == ["pr", "create"]]
    return created


@pytest.mark.parametrize(
    ("reviewed", "review_status"),
    [({}, REVIEW_NOT_RUN), ({"reviewed": True}, "")],
    ids=["review-off", "reviewed"],
)
def test_publish_pushes_the_branch_and_opens_its_pull_request_closing_the_issue(
    tmp_path, monkeypatch, reviewed, review_status
):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    implemented(workspace, BRANCH["branch"], "Closes #7\n\nAdds dark mode.\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | reviewed)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"pr": 43}
        assert git(tmp_path / "origin.git", "rev-parse", BRANCH["branch"]) == git(
            workspace, "rev-parse", "HEAD"
        )
        assert created_pull_request(github)[2:] == [
            "--head",
            "agent/7-add-dark-mode",
            "--base",
            "main",
            "--title",
            "Add dark mode",
            "--body",
            f"Closes #7\n\nAdds dark mode.{review_status}",
            "--reviewer",
            "owner",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("stack", "registered"),
    [
        (None, ["--method", "POST", "repos/{owner}/{repo}/stacks"]),
        (3, ["--method", "POST", "repos/{owner}/{repo}/stacks/3/add"]),
    ],
    ids=["new-stack", "existing-stack"],
)
def test_publish_stacks_a_sub_issues_pull_request_on_its_siblings(
    tmp_path, monkeypatch, stack, registered
):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    implemented(workspace, "agent/8-dark-theme", "Adds the dark theme.\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(8, "Dark theme", parent=5))
    github.answer(
        "repos/{owner}/{repo}/issues",
        output=[[issue(7, "Light theme", parent=5), issue(8, "Dark theme", parent=5)]],
    )
    github.answer(
        "repos/{owner}/{repo}/pulls",
        output=[[pull_request(41, "agent/7-light-theme", closes=7, stack=stack)]],
    )
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await opened(
            dispatcher, 8, {"branch": "agent/8-dark-theme", "base": "agent/7-light-theme"}
        )

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"pr": 43}
        [stacked] = [arguments for arguments in github.calls if "POST" in arguments]
        assert all(argument in stacked for argument in registered)
        pulls = [stacked[at + 1] for at, argument in enumerate(stacked) if argument == "-F"]
        assert pulls == (
            ["pull_requests[]=43"] if stack else ["pull_requests[]=41", "pull_requests[]=43"]
        )

    asyncio.run(scenario())


def test_publish_takes_ready_for_human_off_an_issue_an_earlier_failure_handed_to_a_human(
    tmp_path, monkeypatch
):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    implemented(workspace, BRANCH["branch"], "Adds dark mode.\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode", labels=["ready-for-human"]))
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert github.calls[-1] == ["issue", "edit", "7", "--remove-label", "ready-for-human"]

    asyncio.run(scenario())


def test_publish_titles_the_pull_request_with_the_title_the_session_wrote(tmp_path, monkeypatch):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    implemented(workspace, BRANCH["branch"], "Adds dark mode.\n")
    (workspace / ".scratch" / "pr-title.txt").write_text("Add a dark mode toggle\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH)

        assert result.ending is StepEnding.CLEAN, result.summary
        created = created_pull_request(github)
        assert created[created.index("--title") + 1] == "Add a dark mode toggle"
        assert list((workspace / ".scratch").iterdir()) == []

    asyncio.run(scenario())


def test_publish_pushes_nothing_when_the_client_wrote_no_pull_request_body(tmp_path, monkeypatch):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    implemented(workspace, BRANCH["branch"], None)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "publish_pull_request" failed: MissingPullRequestBody: '
            "The coding client wrote no pull request body to .scratch/pr-body.md."
        )
        assert not any(arguments[:2] == ["pr", "create"] for arguments in github.calls)
        assert git(tmp_path / "origin.git", "branch", "--list", BRANCH["branch"]) == ""

    asyncio.run(scenario())


def blockers(*numbers: int) -> list[list[dict[str, object]]]:
    """The open issues that block one, as GitHub's dependencies API lists them."""
    return [[{"number": number, "state": "open", "parent_issue_url": None} for number in numbers]]


async def scanned(
    hub: Hub, monkeypatch: pytest.MonkeyPatch, delivery: Mapping[str, object] | None = None
) -> tuple[list[FactoryRun], list[ToolResult]]:
    """Fire the coder's scan routine, with *delivery* as its signal, against the served hub.

    Returns the hub's runs of the software factory, and what the scan's code step answered.
    """
    [coder] = hub.registry.factory_members("software")
    environment = instance_environment(hub, coder.instance_id)
    monkeypatch.setenv("KINBY_CONTROL_TOKEN", environment["KINBY_CONTROL_TOKEN"])
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", environment["GITHUB_WEBHOOK_SECRET"])
    instance = load_instance(hub.instances_directory / str(coder.instance_id))
    dispatcher, _ = signal_runtime(instance, RoutineModel())
    payload = (
        None
        if delivery is None
        else {"body": json.dumps(delivery), "content_type": "application/json"}
    )
    events = []
    async with served(hub) as address:
        monkeypatch.setenv(
            "KINBY_INTAKE_URL", url(address, f"/instances/{coder.instance_id}/intake")
        )
        accepted = await call(dispatcher, "routine.run", name="scan", payload=payload)
        assert isinstance(accepted, AcceptedResult), accepted
        stream = await thread_events(dispatcher, {"thread_id": accepted.thread_id}, set(Scope))
        async with asyncio.timeout(10):
            async for event in stream:
                assert isinstance(event, Event)
                events.append(event.payload)
                if is_turn_closing(event.payload):
                    break
        await stream.aclose()
    assert isinstance(events[-1], TurnCompleted), events[-1]
    listed = await hub_client(hub).call(FACTORY_RUN_LIST, FactoryRunListCommand(factory="software"))
    assert isinstance(listed, FactoryRunListResult)
    return listed.runs, [event for event in events if isinstance(event, ToolResult)]


def test_the_scan_hands_the_oldest_eligible_issue_to_the_software_factory(tmp_path, monkeypatch):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    github.answer(
        "repos/{owner}/{repo}/issues",
        output=[
            [
                issue(3, "Has a pull request"),
                issue(4, "Blocked"),
                issue(6, "Eligible"),
                issue(8, "Newer"),
            ]
        ],
    )
    github.answer(
        "repos/{owner}/{repo}/pulls",
        output=[
            [
                pull_request(30, "agent/3-has-a-pull-request", 3),
                pull_request(31, "agent/6-eligible", 6, head_repository="stranger/project"),
                pull_request(32, "agent/6-eligible", 6, head_repository=None),
            ]
        ],
    )
    for number, blocking in ((4, blockers(9)), (6, blockers()), (8, blockers())):
        github.answer(ISSUE.format(number=number) + "/dependencies/blocked_by", output=blocking)
    github.answer(ISSUE.format(number=6), output=issue(6, "Eligible"))

    async def scenario() -> None:
        await started_coder(hub, runtime)
        runs, [answered] = await scanned(hub, monkeypatch)

        assert [run.work_item for run in runs] == [{"issue": 6}]
        assert answered.error is False, answered.output
        assert not any(arguments[-1:] == [ISSUE.format(number=8)] for arguments in github.calls)

    asyncio.run(scenario())


def test_the_scan_reads_an_issue_just_labeled_that_the_issue_list_does_not_show_yet(
    tmp_path, monkeypatch
):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    github.answer("repos/{owner}/{repo}/issues", output=[[issue(8, "Listed")]])
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    for number in (5, 8):
        github.answer(ISSUE.format(number=number) + "/dependencies/blocked_by", output=blockers())
    github.answer(ISSUE.format(number=5), output=issue(5, "Just labeled"))
    labeled = {
        "action": "labeled",
        "label": {"name": "ready-for-agent"},
        "issue": {"number": 5},
    }

    async def scenario() -> None:
        await started_coder(hub, runtime)
        runs, _ = await scanned(hub, monkeypatch, labeled)

        assert [run.work_item for run in runs] == [{"issue": 5}]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "delivery",
    [
        {"action": "created", "comment": {"body": "Looks good."}, "issue": {"number": 6}},
        {
            "action": "submitted",
            "review": {"state": "commented"},
            "pull_request": {"number": 42, "head": {"ref": "agent/7-add-dark-mode"}},
        },
        {"action": "completed", "check_suite": {"head_branch": "agent/7-add-dark-mode"}},
    ],
    ids=["comment", "review", "check-suite"],
)
def test_a_signal_that_cannot_change_which_issue_is_eligible_hands_nothing_and_asks_nothing(
    tmp_path, monkeypatch, delivery
):
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", runtime=runtime)
    github = FakeGitHub(tmp_path / "github", monkeypatch)

    async def scenario() -> None:
        await started_coder(hub, runtime)
        runs, _ = await scanned(hub, monkeypatch, delivery)

        assert runs == []
        assert github.calls == []

    asyncio.run(scenario())


def with_review(factory: str) -> str:
    """The software factory's file with its fix and review steps uncommented."""
    return re.sub(r"(?m)^  # (?=- |  )", "  ", factory)


def test_turning_review_on_sends_changes_back_to_a_fix_that_resumes_the_implementers_session(
    tmp_path,
):
    control = FakeControl()
    idle = StepResult(ending=StepEnding.CLEAN, summary="Nothing to fix yet.", session="claude-1")
    changes = StepResult(ending=StepEnding.CLEAN, outcome="changes", values={"reviewed": True})
    fixed = StepResult(ending=StepEnding.CLEAN, summary="Fixed the finding.", session="claude-1")
    clean = StepResult(ending=StepEnding.CLEAN, outcome="clean", values={"reviewed": True})
    control.step_results = [
        PREPARED,
        SET_UP,
        IMPLEMENTED,
        NOTHING_TO_ANSWER,
        idle,
        changes,
        fixed,
        clean,
        CHECKED,
        OPENED,
    ]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        shipped = await client.call(FACTORY_GET, FactoryGetCommand(name="software"))
        assert isinstance(shipped, FactoryResult)
        edited = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="software",
                files={"factory.yaml": with_review(shipped.files["factory.yaml"])},
                hash=shipped.hash,
            ),
        )
        assert isinstance(edited, FactoryResult), edited
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        waiting = await babysitting(hub, run.run_id)

        assert [attempt.step for attempt in waiting.attempts] == [
            "prepare",
            "setup",
            "implement",
            "answer",
            "fix",
            "review",
            "fix",
            "review",
            "checks",
            "publish",
            "babysit",
        ]
        fix = coding("fix", 30, resume="claude-1")
        fixes = [command.step for _, command in control.steps if command.origin.step == "fix"]
        assert fixes == [fix, fix]
        _, publish = control.steps[-1]
        assert publish.results["reviewed"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("verdict", "ending", "outcome"),
    [
        ("clean\n", StepEnding.CLEAN, "clean"),
        ("changes\n[hard] theme.py:1 The ticket asks for a toggle.\n", StepEnding.CLEAN, "changes"),
        (None, StepEnding.FAILED, None),
        ("Looks fine to me.\n", StepEnding.FAILED, None),
    ],
    ids=["clean", "changes", "no-verdict", "no-verdict-line"],
)
def test_the_review_hook_reads_the_verdict_the_reviewer_wrote_never_what_it_said(
    tmp_path, monkeypatch, verdict, ending, outcome
):
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    implemented(workspace, BRANCH["branch"], "Adds dark mode.\n")
    writes = "" if verdict is None else f"open('.scratch/review.md', 'w').write({verdict!r})\n"
    stub_client(tmp_path, monkeypatch, "claude", writes + streaming(CLAUDE_STREAM))
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await run_in(
            dispatcher,
            StepRunCommand(
                step=ClientStepRun(
                    client=CodingClient.CLAUDE, prompt="Review it.", timeout_seconds=30
                ),
                hook="read_review_verdict",
                origin=origin("review"),
                work_item={"issue": 7},
                results=BRANCH,
            ),
        )

        assert result.ending is ending, result.summary
        assert result.outcome == outcome
        if ending is StepEnding.CLEAN:
            assert result.values == {"reviewed": True}
        else:
            assert "UnreadableVerdict: .scratch/review.md" in result.summary

    asyncio.run(scenario())


PULL = {"number": 42, "head": {"ref": BRANCH["branch"], "sha": "head2"}, "user": {"login": "coder"}}


def review_thread(
    thread: str, *comments: tuple[str, str], resolved: bool = False
) -> dict[str, object]:
    """A review thread as GitHub's GraphQL API answers with it, each comment as (author,
    author_association)."""
    return {
        "id": thread,
        "isResolved": resolved,
        "path": "theme.py",
        "line": 1,
        "comments": {
            "nodes": [
                {
                    "author": {"login": author},
                    "authorAssociation": association,
                    "body": f"{author} says.",
                }
                for author, association in comments
            ]
        },
    }


def submitted(author: str, association: str, commit: str = "head2") -> dict[str, object]:
    """A review as GitHub's REST API lists it."""
    return {"user": {"login": author}, "author_association": association, "commit_id": commit}


def answer_pull_request(
    github: FakeGitHub,
    *,
    checks: Sequence[str] = ("completed",),
    threads: Sequence[dict[str, object]] = (),
    reviews: Sequence[dict[str, object]] = (),
) -> None:
    """Answer what assess reads of pull request 42 as the coder: its check runs, review threads
    and reviews."""
    github.answer("repos/{owner}/{repo}/pulls/42/reviews", output=[list(reviews)])
    github.answer("repos/{owner}/{repo}/pulls/42", output=PULL)
    github.answer(
        "repos/{owner}/{repo}/commits/head2/check-runs",
        output=[{"total_count": len(checks), "check_runs": [{"status": s} for s in checks]}],
    )
    github.answer(
        "graphql",
        output=[{"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}],
    )
    github.answer("user", "--jq", output="coder")
    github.answer("repo", "view", output=REPOSITORY)


async def assessed(dispatcher: ScheduledDispatcher) -> StepResult:
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="assess_pull_request"),
            origin=origin("assess"),
            work_item={"issue": 7},
            results=BRANCH | {"pr": 42},
        ),
    )


def labelled(github: FakeGitHub) -> list[list[str]]:
    return [arguments for arguments in github.calls if arguments[:2] == ["pr", "edit"]]


def test_assess_labels_a_pull_request_a_trusted_reviewer_reviewed_on_its_head_merge_ready(
    tmp_path, monkeypatch
):
    instance = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        threads=[
            review_thread("T1", ("owner", "OWNER"), ("coder", "OWNER")),
            review_thread("T2", ("stranger", "NONE"), resolved=True),
        ],
        reviews=[submitted("greptile-apps[bot]", "NONE")],
    )
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert (result.outcome, result.values) == ("merge-ready", {"merge_ready": True})
        assert labelled(github) == [
            ["pr", "edit", "42", "--add-label", "merge-ready", "--add-reviewer", "owner"]
        ]
        assert not any("merge" in arguments for arguments in github.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("checks", "reviews"),
    [
        (("completed", "in_progress"), [submitted("owner", "OWNER")]),
        (("completed",), [submitted("owner", "OWNER", commit="head1")]),
        (("completed",), [submitted("stranger", "NONE")]),
        (("completed",), [submitted("coder", "OWNER")]),
    ],
    ids=["check-running", "earlier-head", "untrusted-reviewer", "own-replies"],
)
def test_assess_waits_until_a_trusted_reviewer_reviews_the_head_and_no_check_runs(
    tmp_path, monkeypatch, checks, reviews
):
    instance = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, checks=checks, reviews=reviews)
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert (result.outcome, result.values) == ("waiting", {})
        assert labelled(github) == []

    asyncio.run(scenario())


def test_assess_hands_a_fix_round_every_actionable_thread_trusted_authors_wrote(
    tmp_path, monkeypatch
):
    instance = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        threads=[
            review_thread("T1", ("owner", "OWNER"), ("coder", "OWNER"), ("owner", "OWNER")),
            review_thread("T2", ("greptile-apps", "NONE")),
            review_thread("T3", ("helper", "COLLABORATOR"), ("coder", "OWNER")),
            review_thread("T4", ("owner", "OWNER"), resolved=True),
            review_thread("T5", ("stranger", "NONE")),
        ],
        reviews=[submitted("owner", "OWNER")],
    )
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == "feedback"
        assert result.values["pr"] == 42
        assert json.loads(str(result.values["feedback"])) == [
            {
                "id": "T1",
                "path": "theme.py",
                "line": 1,
                "comments": [
                    {"author": "owner", "body": "owner says."},
                    {"author": "coder", "body": "coder says."},
                    {"author": "owner", "body": "owner says."},
                ],
            },
            {
                "id": "T2",
                "path": "theme.py",
                "line": 1,
                "comments": [{"author": "greptile-apps", "body": "greptile-apps says."}],
            },
        ]
        assert labelled(github) == []

    asyncio.run(scenario())


def test_assess_answers_the_codex_review_app_like_a_trusted_reviewer(tmp_path, monkeypatch):
    instance = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        threads=[review_thread("T1", ("chatgpt-codex-connector", "NONE"))],
        reviews=[submitted("chatgpt-codex-connector[bot]", "NONE")],
    )
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == "feedback"
        assert [thread["id"] for thread in json.loads(str(result.values["feedback"]))] == ["T1"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "thread",
    [
        review_thread("T5", ("stranger", "NONE")),
        review_thread("T6", ("owner", "OWNER"), ("stranger", "CONTRIBUTOR"), ("owner", "OWNER")),
    ],
    ids=["wrote-it", "joined-it"],
)
def test_feedback_from_an_untrusted_author_starts_no_fix_round_and_needs_a_human(
    tmp_path, monkeypatch, thread
):
    instance = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, threads=[thread], reviews=[submitted("owner", "OWNER")])
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "assess_pull_request" failed: UntrustedFeedback: '
            "Review feedback from untrusted authors needs a human: stranger."
        )
        assert result.values == {}
        assert labelled(github) == []

    asyncio.run(scenario())


def pushed_branch(tmp_path: Path) -> Instance:
    """The coder with its workspace on the run's branch, which origin holds, and scratch files
    excluded as the prepare step leaves them."""
    instance = coder_at(tmp_path)
    workspace = instance.manifest.workspace.path
    implemented(workspace, BRANCH["branch"], None)
    git(workspace, "push", "-u", "origin", BRANCH["branch"])
    (workspace / ".git" / "info" / "exclude").write_text(".scratch/\n")
    return instance


def writes_replies(replies: object) -> str:
    """What the stub client runs to write its replies to the review threads."""
    return (
        "import os\n"
        "os.makedirs('.scratch', exist_ok=True)\n"
        f"open('.scratch/review-replies.json', 'w').write({json.dumps(replies)!r})\n"
    )


FIXES = (
    "import subprocess\n"
    "open('theme.py', 'w').write('DARK_MODE = True\\n')\n"
    "subprocess.run(['git', 'commit', '-am', 'Rename DARK'], check=True, capture_output=True)\n"
)
FIXED_T1 = {"T1": {"fixed": True, "reply": "Renamed it."}}


@pytest.mark.parametrize(
    ("then", "results", "ending", "reason"),
    [
        (FIXES + writes_replies(FIXED_T1), {"feedback": THREADS}, StepEnding.CLEAN, None),
        (
            writes_replies({"T1": {"fixed": False, "reply": "DARK is the house style."}}),
            {"feedback": THREADS},
            StepEnding.CLEAN,
            None,
        ),
        ("", {}, StepEnding.CLEAN, None),
        (
            writes_replies(FIXED_T1),
            {"feedback": THREADS},
            StepEnding.FAILED,
            "UncommittedWork: The replies say a thread is fixed, but agent/7-add-dark-mode has "
            "no new commit.",
        ),
        (
            FIXES,
            {"feedback": THREADS},
            StepEnding.FAILED,
            "UnansweredThreads: .scratch/review-replies.json holds no replies",
        ),
        (
            FIXES + writes_replies({"T9": {"fixed": True, "reply": "Renamed it."}}),
            {"feedback": THREADS},
            StepEnding.FAILED,
            "UnansweredThreads: .scratch/review-replies.json does not reply to exactly the "
            "threads T1.",
        ),
    ],
    ids=[
        "fixed",
        "explained",
        "no-feedback-yet",
        "fix-not-committed",
        "no-replies",
        "wrong-thread",
    ],
)
def test_the_answer_hook_records_a_reply_for_every_thread_once_its_fixes_are_committed(
    tmp_path, monkeypatch, then, results, ending, reason
):
    instance = pushed_branch(tmp_path)
    workspace = instance.manifest.workspace.path
    stub_client(tmp_path, monkeypatch, "claude", then + streaming(CLAUDE_STREAM))
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await run_in(
            dispatcher,
            StepRunCommand(
                step=ClientStepRun(
                    client=CodingClient.CLAUDE, prompt="Answer them.", timeout_seconds=30
                ),
                hook="check_answers",
                origin=origin("answer"),
                work_item={"issue": 7},
                results=BRANCH | {"pr": 42} | results,
            ),
        )

        assert result.ending is ending, result.summary
        assert not (workspace / ".scratch" / "review-replies.json").exists()
        if reason is not None:
            assert f'Hook "check_answers" failed: {reason}' in result.summary
        elif "feedback" in results:
            replies = json.loads(str(result.values["replies"]))
            assert set(replies) == {"T1"}
        else:
            assert result.values == {}

    asyncio.run(scenario())


def test_publish_after_a_fix_round_pushes_it_and_replies_on_each_thread_it_answered(
    tmp_path, monkeypatch
):
    instance = pushed_branch(tmp_path)
    workspace = instance.manifest.workspace.path
    commit(workspace, "theme.py", "DARK_MODE = True\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    replies = FIXED_T1 | {"T2": {"fixed": False, "reply": "DARK is the house style."}}
    dispatcher, _ = signal_runtime(instance, RoutineModel())

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | {"pr": 42, "replies": json.dumps(replies)})

        head = git(workspace, "rev-parse", "HEAD")
        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.summary == "Pushed agent/7-add-dark-mode and replied on 2 review threads."
        assert git(tmp_path / "origin.git", "rev-parse", BRANCH["branch"]) == head
        graphql = [
            {
                value.partition("=")[0]: value.partition("=")[2]
                for flag, value in pairwise(arguments)
                if flag == "-f"
            }
            for arguments in github.calls
            if "graphql" in arguments
        ]
        assert [
            ("resolveReviewThread" in call["query"], call["thread"], call.get("body"))
            for call in graphql
        ] == [
            (False, "T1", f"{head}: Renamed it."),
            (True, "T1", None),
            (False, "T2", "DARK is the house style."),
        ]
        assert not any(
            arguments[:2] in (["pr", "create"], ["pr", "edit"]) for arguments in github.calls
        )

    asyncio.run(scenario())


def test_publish_after_a_fix_round_applies_the_title_and_body_the_session_rewrote_once(
    tmp_path, monkeypatch
):
    instance = pushed_branch(tmp_path)
    workspace = instance.manifest.workspace.path
    commit(workspace, "theme.py", "DARK_MODE = True\n")
    (workspace / ".scratch").mkdir()
    (workspace / ".scratch" / "pr-title.txt").write_text("Add a dark mode named DARK_MODE\n")
    (workspace / ".scratch" / "pr-body.md").write_text("Closes #7\n\nAdds DARK_MODE.\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    dispatcher, _ = signal_runtime(instance, RoutineModel())
    fix_round = BRANCH | {"pr": 42, "replies": json.dumps(FIXED_T1)}

    async def scenario() -> None:
        first = await opened(dispatcher, 7, fix_round)
        second = await opened(dispatcher, 7, fix_round)

        assert (first.ending, second.ending) == (StepEnding.CLEAN, StepEnding.CLEAN)
        edits = [arguments for arguments in github.calls if arguments[:2] == ["pr", "edit"]]
        assert edits == [
            [
                "pr",
                "edit",
                "42",
                "--title",
                "Add a dark mode named DARK_MODE",
                "--body",
                f"Closes #7\n\nAdds DARK_MODE.{REVIEW_NOT_RUN}",
            ]
        ]

    asyncio.run(scenario())
