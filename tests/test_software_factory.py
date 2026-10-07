"""The software factory kinby ships: its file on the hub, and its coder's tools and hooks."""

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
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
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunOrigin,
    FactoryRunStatus,
    InstanceStartCommand,
    LifecycleOperationResult,
    OperationState,
    Scope,
    StepEnding,
    StepResult,
    StepRunCommand,
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
from tests.test_factory_runs import intake_client, settled
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
CHECKED = StepResult(ending=StepEnding.CLEAN, summary="Every command exited with code 0.")
OPENED = StepResult(ending=StepEnding.CLEAN, values={"pr": 42})


def test_the_software_factory_carries_an_issue_from_intake_to_an_opened_pull_request(tmp_path):
    control = FakeControl()
    control.step_results = [PREPARED, IMPLEMENTED, CHECKED, OPENED]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [(attempt.step, attempt.ending) for attempt in finished.attempts] == [
            ("prepare", StepEnding.CLEAN),
            ("implement", StepEnding.CLEAN),
            ("checks", StepEnding.CLEAN),
            ("open-pr", StepEnding.CLEAN),
        ]
        prepare, implement, checks, open_pr = (command for _, command in control.steps)
        assert prepare.step == CodeStepRun(call="prepare_branch")
        assert implement.step == ClientStepRun(
            client=CodingClient.CLAUDE,
            prompt=(SHIPPED_FACTORIES / "software" / "prompts" / "implement.md").read_text(),
            timeout_seconds=60 * 60,
        )
        assert implement.hook == "check_implementation"
        assert implement.results == PREPARED.values
        assert isinstance(checks.step, CommandStepRun)
        assert open_pr.step == CodeStepRun(call="open_pull_request")
        assert open_pr.work_item == {"issue": 7}
        assert open_pr.results == PREPARED.values
        assert finished.attempts[-1].values == {"pr": 42}

    asyncio.run(scenario())


def test_opening_the_pull_request_is_never_retried_and_needs_human_reports_on_the_issue(
    tmp_path,
):
    control = FakeControl()
    refused = StepResult(
        ending=StepEnding.FAILED,
        summary='Tool "open_pull_request" failed: gh exited with code 1: GitHub is down.',
    )
    control.step_results = [PREPARED, IMPLEMENTED, CHECKED, refused]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        stopped = await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            while len(control.steps) < 5:
                await asyncio.sleep(0.01)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "open-pr")
        assert [attempt.step for attempt in stopped.attempts].count("open-pr") == 1
        _, report = control.steps[-1]
        assert report.step == CodeStepRun(call="report_needs_human", summary=refused.summary)
        assert (report.origin.run_id, report.origin.step) == (run.run_id, "open-pr")
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
) -> dict[str, object]:
    """An open issue labeled ready-for-agent, as GitHub's REST API answers with it."""
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/owner/project/issues/{number}",
        "state": "open",
        "labels": [{"name": "ready-for-agent"}],
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
    number: int, branch: str, closes: int, stack: int | None = None
) -> dict[str, object]:
    """An open agent pull request, as GitHub's REST API lists it."""
    return {
        "number": number,
        "html_url": f"https://github.com/owner/project/pull/{number}",
        "head": {"ref": branch, "sha": "abc123"},
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
    dispatcher: ScheduledDispatcher, number: int, results: Mapping[str, str | bool]
) -> StepResult:
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="open_pull_request"),
            origin=origin("open-pr"),
            work_item={"issue": number},
            results=dict(results),
        ),
    )


def created_pull_request(github: FakeGitHub) -> list[str]:
    [created] = [arguments for arguments in github.calls if arguments[:2] == ["pr", "create"]]
    return created


@pytest.mark.parametrize(
    ("reviewed", "review_status"),
    [
        (
            {},
            "\n\n## Review status\n\n"
            "Adversarial review was not run. Review happens on this pull request.",
        ),
        ({"reviewed": True}, ""),
    ],
    ids=["review-off", "reviewed"],
)
def test_open_pr_pushes_the_branch_and_opens_its_pull_request_closing_the_issue(
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
def test_open_pr_stacks_a_sub_issues_pull_request_on_its_siblings(
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


def test_open_pr_pushes_nothing_when_the_client_wrote_no_pull_request_body(tmp_path, monkeypatch):
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
            'Tool "open_pull_request" failed: MissingPullRequestBody: '
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
        "repos/{owner}/{repo}/pulls", output=[[pull_request(30, "agent/3-has-a-pull-request", 3)]]
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


def test_a_signal_that_cannot_change_which_issue_is_eligible_hands_nothing_and_asks_nothing(
    tmp_path, monkeypatch
):
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", runtime=runtime)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    commented = {"action": "created", "comment": {"body": "Looks good."}, "issue": {"number": 6}}

    async def scenario() -> None:
        await started_coder(hub, runtime)
        runs, _ = await scanned(hub, monkeypatch, commented)

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
    control.step_results = [PREPARED, IMPLEMENTED, idle, changes, fixed, clean, CHECKED, OPENED]
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
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [attempt.step for attempt in finished.attempts] == [
            "prepare",
            "implement",
            "fix",
            "review",
            "fix",
            "review",
            "checks",
            "open-pr",
        ]
        fix = ClientStepRun(
            client=CodingClient.CLAUDE,
            prompt=(SHIPPED_FACTORIES / "software" / "prompts" / "fix.md").read_text(),
            resume="claude-1",
            timeout_seconds=15 * 60,
        )
        fixes = [command.step for _, command in control.steps if command.origin.step == "fix"]
        assert fixes == [fix, fix]
        _, open_pr = control.steps[-1]
        assert open_pr.results["reviewed"] is True

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
