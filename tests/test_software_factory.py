"""The software factory kinby ships: its file on the hub, its runs on a line, and its tools and
checks on a workbench."""

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    FACTORY_CHECK,
    FACTORY_EDIT,
    FACTORY_GET,
    LINE_HAND_IN,
    LINE_SETUP,
    LINE_SETUP_DESCRIBE,
    WORKBENCH_START,
    ClientStepRun,
    CodeStepRun,
    CodingClient,
    CommandStepRun,
    FactoryCheckCommand,
    FactoryCheckResult,
    FactoryEditCommand,
    FactoryGetCommand,
    FactoryResult,
    FactoryRun,
    FactoryRunDetail,
    FactoryRunOrigin,
    FactoryRunStatus,
    IntakeRunCommand,
    Line,
    LineHandInCommand,
    LineSetupCommand,
    LineSetupDescribeCommand,
    LineSetupDescription,
    LineSetupResult,
    OperationState,
    ReasoningEffort,
    RunFolder,
    StepEnding,
    StepResult,
    StepRunCommand,
    StepValue,
    WorkbenchCreateCommand,
    WorkbenchStartCommand,
)
from kinby.core.run_worktrees import cut_run_worktree
from kinby.factories import SHIPPED_FACTORIES
from kinby.hub import Hub
from kinby.workbench import WorkbenchSteps
from tests.test_contract_server import served as instance_served
from tests.test_factory_runs import detail, settled
from tests.test_factory_waits import signalled
from tests.test_hub import FakeControl, FakeImages, FakeRuntime, hub_client
from tests.test_hub_line_stops import _started_home
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url
from tests.test_hub_workbench_workspace import _commands_set
from tests.test_hub_workbenches import created, finished
from tests.test_routines import RoutineModel, instance_at, signal_runtime
from tests.test_steps import CLAUDE_STREAM, streaming, stub_client

FACTORY = SHIPPED_FACTORIES / "software"
SUGGESTED_PICKS = [
    "adversarial-review",
    "api-and-interface-design",
    "codebase-design",
    "no-comments",
    "ponytail",
    "pr",
    "tdd",
    "unslop",
]
#: The skills of the factory's own folder, which reach its agent steps beside the picks.
FACTORY_SKILLS = {"implement-ticket", "repository-survey"}
#: The workbench of the repository the factory works in, holding the secrets it requires.
PROJECT = WorkbenchCreateCommand(
    name="project",
    recipe="coder",
    workspace_source="https://github.com/owner/project.git",
    secrets={
        "GH_TOKEN": "ghp-test",
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


def test_the_software_factory_passes_the_factory_check_in_the_file_that_requires_a_workbench(
    tmp_path,
):
    client = hub_client(fresh_hub(tmp_path / "hub"))

    async def scenario() -> FactoryCheckResult:
        checked = await client.call(FACTORY_CHECK, FactoryCheckCommand(name="software"))
        assert isinstance(checked, FactoryCheckResult)
        return checked

    assert asyncio.run(scenario()).problems == []
    declared = (FACTORY / "factory.yaml").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*-? ?(instances|in|branch|hook):", declared)
    assert not (FACTORY / "instances").exists()


def test_the_factory_suggests_the_eight_shipped_skills_and_holds_its_own_two(tmp_path):
    client = hub_client(fresh_hub(tmp_path / "hub"))

    async def scenario() -> LineSetupDescription:
        described = await client.call(
            LINE_SETUP_DESCRIBE, LineSetupDescribeCommand(factory="software")
        )
        assert isinstance(described, LineSetupDescription), described
        return described

    described = asyncio.run(scenario())
    assert described.suggested_picks == SUGGESTED_PICKS
    assert [field.name for field in described.secrets] == [
        "GH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GIT_USER_NAME",
        "GIT_USER_EMAIL",
    ]
    assert described.commands == ["setup", "checks"]
    skills = FACTORY / "skills"
    assert {folder.name for folder in skills.iterdir() if folder.is_dir()} == FACTORY_SKILLS


@pytest.mark.parametrize("name", sorted(FACTORY_SKILLS))
def test_a_factory_skill_names_no_skill_a_workbench_may_not_have_picked(name):
    text = (FACTORY / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    named = [
        name
        for name in SUGGESTED_PICKS
        if re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text)
    ]

    assert named == []


def test_no_shipped_prompt_names_a_skill_but_implement_ticket():
    skills = {*SUGGESTED_PICKS, *FACTORY_SKILLS}

    for prompt in sorted((FACTORY / "prompts").glob("*.md")):
        sent = prompt.read_text(encoding="utf-8")
        named = {
            name for name in skills if re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", sent)
        }
        assert named <= {"implement-ticket"}, prompt.name
        assert "SKILL.md" not in sent, prompt.name


async def software_line(hub: Hub, runtime: FakeRuntime) -> Line:
    """The software factory set up under the home instance on a started workbench of the
    project, which holds its setup and checks lists.

    The home instance runs in this process while the setup writes the line's intake routine in
    it, and a delivery later reaches a stand-in for it.
    """
    home = await _started_home(hub)
    client = hub_client(hub)
    workbench_id = (await created(client, PROJECT)).workbench_id
    await _commands_set(client, workbench_id, "setup", ["uv sync --frozen"])
    await _commands_set(client, workbench_id, "checks", ["bun run check"])
    start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
    assert (await finished(client, start)).state is OperationState.SUCCEEDED
    runtime.addresses[str(workbench_id)] = f"http://kinby-{workbench_id}:8787"
    directory = hub.instances_directory.parent / "served-home"
    directory.mkdir()
    instance = instance_at(directory)
    dispatcher, _ = signal_runtime(instance, RoutineModel())
    token = control_token(hub, str(home))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("KINBY_CONTROL_TOKEN", token)
        patch.setenv("KINBY_HUB_SCOPES", ",".join(hub.instance_scopes(home)))
        async with (
            served(hub) as address,
            instance_served(instance, dispatcher, token=token) as own,
        ):
            patch.setenv("KINBY_HUB_URL", url(address, f"/instances/{home}/hub"))
            runtime.addresses[str(home)] = f"http://{own.host}:{own.port}"
            set_up = await client.call(
                LINE_SETUP, LineSetupCommand(factory="software", workbench_id=workbench_id)
            )
    assert isinstance(set_up, LineSetupResult), set_up
    return set_up.line


async def handed_in(hub: Hub, line: Line, issue: int) -> FactoryRun:
    run = await hub_client(hub).call(
        LINE_HAND_IN, LineHandInCommand(line_id=line.line_id, work_item={"issue": issue})
    )
    assert isinstance(run, FactoryRun), run
    return run


#: The agent branch the factory's worktree call names for issue 7, and the base it starts from.
BRANCH = {"branch": "agent/7-add-dark-mode", "base": "main"}
#: The folder the first step of a run asks its workbench to name and cut.
NAMED = RunFolder(branch=None, named_by="agent_branch")
#: The run worktree every later step works in.
WORKTREE = RunFolder(branch="agent/7-add-dark-mode", base="main")
#: What each step of a run that goes well hands back, in the order the steps run. The first
#: step's carries the branch and base its workbench named for the run worktree.
VETTED = StepResult(ending=StepEnding.CLEAN, values=BRANCH)
IMPLEMENTED = StepResult(ending=StepEnding.CLEAN, summary="Added dark mode.", session="claude-1")
#: The answer step before the pull request opens, with no review threads to answer yet.
NOTHING_TO_ANSWER = StepResult(ending=StepEnding.CLEAN, summary="No review threads yet.")
CHECKED = StepResult(ending=StepEnding.CLEAN, summary="Every command exited with code 0.")
SET_UP = CHECKED
#: The fix step when no check failed: it changes nothing.
NOTHING_TO_FIX = StepResult(ending=StepEnding.CLEAN, summary="Nothing failed.", session="claude-1")
#: Every step up to the pull request, the checks passing at the first try.
CHECKED_CHANGE = [VETTED, SET_UP, IMPLEMENTED, NOTHING_TO_ANSWER, NOTHING_TO_FIX, CHECKED]
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


async def woken(hub: Hub, runtime: FakeRuntime, line: Line, body: bytes, event: str) -> None:
    """Deliver a GitHub webhook to the line's intake in its owner, which accepts it."""
    assert line.intake is not None
    status, _ = await signalled(hub, runtime, line.owner_id, body, routine=line.intake, event=event)
    assert status == 202


def coding(step: str, minutes: int, resume: str | None = None) -> ClientStepRun:
    """What the hub asks the workbench to run for one of the factory's agent steps."""
    return ClientStepRun(
        client=CodingClient.CLAUDE,
        prompt=(FACTORY / "prompts" / f"{step}.md").read_text(),
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
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        waiting = await babysitting(hub, run.run_id)

        assert [(attempt.step, attempt.ending) for attempt in waiting.attempts] == [
            ("ticket", StepEnding.CLEAN),
            ("setup", StepEnding.CLEAN),
            ("implement", StepEnding.CLEAN),
            ("answer", StepEnding.CLEAN),
            ("fix", StepEnding.CLEAN),
            ("checks", StepEnding.CLEAN),
            ("publish", StepEnding.CLEAN),
            ("babysit", None),
        ]
        ticket, setup, implement, answer, fix, checks, publish = (
            command for _, command in control.steps
        )
        assert (ticket.step, ticket.folder) == (CodeStepRun(call="vet_ticket"), NAMED)
        assert [
            command.folder for command in (setup, implement, answer, fix, checks, publish)
        ] == 6 * [WORKTREE]
        assert setup.step == CommandStepRun(run=["uv sync --frozen"])
        assert implement.step == coding("implement", 60)
        assert implement.hook == "check_implementation"
        assert implement.results == BRANCH
        assert answer.step == coding("answer", 60, resume="claude-1")
        assert answer.hook == "check_answers"
        assert fix.step == coding("fix", 30, resume="claude-1")
        assert fix.hook == "check_implementation"
        assert checks.step == CommandStepRun(run=["uv sync --frozen", "bun run check"])
        assert publish.step == CodeStepRun(call="publish_pull_request")
        assert publish.work_item == {"issue": 7}
        assert publish.results == BRANCH
        assert waiting.attempts[-2].values == {"pr": 42}

    asyncio.run(scenario())


def test_a_ticket_an_untrusted_author_wrote_stops_the_run_at_its_first_step(tmp_path):
    control = FakeControl()
    untrusted = StepResult(
        ending=StepEnding.FAILED,
        summary='Tool "vet_ticket" failed: UntrustedTicket: '
        "Ticket text from untrusted authors needs a human: stranger.",
        values=BRANCH,
    )
    control.step_results = [untrusted]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        stopped = await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "ticket")
        assert [attempt.step for attempt in stopped.attempts] == ["ticket"]
        _, report = control.steps[-1]
        assert report.step == CodeStepRun(call="report_needs_human", summary=untrusted.summary)
        assert report.folder == WORKTREE

    asyncio.run(scenario())


def test_a_review_wakes_the_run_for_a_fix_round_and_it_finishes_once_merge_ready(
    tmp_path,
):
    control = FakeControl()
    wrote = {"title": "Add dark mode", "body": "Adds dark mode."}
    rewrote = {"title": "", "body": "Adds DARK_MODE."}
    control.step_results = [
        VETTED,
        SET_UP,
        IMPLEMENTED.model_copy(update={"values": wrote}),
        NOTHING_TO_ANSWER,
        NOTHING_TO_FIX,
        CHECKED,
        OPENED,
        FEEDBACK,
        ANSWERED.model_copy(update={"values": ANSWERED.values | rewrote}),
        NOTHING_TO_FIX,
        CHECKED,
        REPLIED,
        MERGE_READY,
    ]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, line, REVIEWED, "pull_request_review")
        await babysitting(hub, run.run_id, times=2)
        await woken(hub, runtime, line, CHECKS_DONE, "check_suite")
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [attempt.step for attempt in finished.attempts] == [
            "ticket",
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
        # The ended call that follows the run's end is no attempt at a step.
        for _, command in control.steps:
            if command.attempt is not None:
                asked.setdefault(command.origin.step, []).append(command)
        assert [command.step for command in asked["assess"]] == [
            CodeStepRun(call="assess_pull_request")
        ] * 2
        assert asked["assess"][0].results["pr"] == 42
        assert asked["answer"][1].results["feedback"] == THREADS
        assert asked["publish"][1].results["replies"] == REPLIES
        assert asked["publish"][1].results["pr"] == 42
        first, second = (
            {name: command.results[name] for name in ("title", "body")}
            for command in asked["publish"]
        )
        assert (first, second) == (wrote, rewrote)
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
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, line, REVIEWED, "pull_request_review")
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


CI_FAILED = (
    "Checks failed on the pull request's head:\n"
    "- pytest (failure): https://github.com/owner/project/runs/pytest"
)
CHECKS_FAILED = StepResult(
    ending=StepEnding.CLEAN,
    summary=CI_FAILED,
    outcome="checks-failed",
    values={"failure": CI_FAILED, "pr": 42},
)
REPAIRED = StepResult(ending=StepEnding.CLEAN, summary="Fixed the test.", session="claude-1")
PUSHED = StepResult(ending=StepEnding.CLEAN, summary="Pushed agent/7-add-dark-mode.")
#: A check repair sent back from babysitting: the failed checks, no thread to answer, their
#: repair, pushed.
CHECK_REPAIR = [CHECKS_FAILED, NOTHING_TO_ANSWER, REPAIRED, CHECKED, PUSHED]


LOCAL_FAILED = StepResult(
    ending=StepEnding.FAILED, summary='"bun run check" exited with code 1.\nruff: F401'
)


@pytest.mark.parametrize(
    ("rounds", "repair", "failure"),
    [
        ([], CHECK_REPAIR, CI_FAILED),
        (FIX_ROUND, CHECK_REPAIR, CI_FAILED),
        ([], [*CHECK_REPAIR[:3], LOCAL_FAILED, *CHECK_REPAIR[2:]], LOCAL_FAILED.summary),
    ],
    ids=["first", "after-a-fix-round", "its-checks-failed"],
)
def test_a_check_that_failed_on_the_pull_request_is_repaired_and_pushed_to_it(
    tmp_path, rounds, repair, failure
):
    control = FakeControl()
    control.step_results = [*CHECKED_CHANGE, OPENED, *rounds, *repair]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)
    waits = 1 + bool(rounds)

    async def scenario() -> None:
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        for times in range(1, waits + 1):
            await babysitting(hub, run.run_id, times)
            await woken(hub, runtime, line, CHECKS_DONE, "check_suite")
        waiting = await babysitting(hub, run.run_id, waits + 1)

        assert [attempt.step for attempt in waiting.attempts][-3:] == [
            "checks",
            "publish",
            "babysit",
        ]
        fixes = [command for _, command in control.steps if command.origin.step == "fix"]
        assert fixes[-1].step == coding("fix", 30, resume="claude-1")
        assert fixes[-1].results["failure"] == failure
        _, publish = control.steps[-1]
        assert publish.step == CodeStepRun(call="publish_pull_request")
        assert publish.results["pr"] == 42
        assert json.loads(str(publish.results.get("replies", "{}"))) == {}

    asyncio.run(scenario())


def test_the_third_failed_check_on_the_pull_request_leaves_the_run_needs_human(tmp_path):
    control = FakeControl()
    control.step_results = [*CHECKED_CHANGE, OPENED, *CHECK_REPAIR * 2, CHECKS_FAILED]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        for times in range(1, 4):
            await babysitting(hub, run.run_id, times)
            await woken(hub, runtime, line, CHECKS_DONE, "check_suite")
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "assess")
        assert stopped.attempts[-1].summary == (
            f'{CI_FAILED}\nStep "assess" sent the work back to "answer" 2 times, its max.'
        )

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
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, line, CHECKS_DONE, "check_suite")
        again = await babysitting(hub, run.run_id, times=2)
        await woken(hub, runtime, line, REVIEWED, "pull_request_review")
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
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, line, replied, "pull_request_review_comment")
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [attempt.step for attempt in finished.attempts][-2:] == ["babysit", "assess"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("assessment", "status"),
    [
        (
            StepResult(ending=StepEnding.CLEAN, outcome="merged", values={"merge_ready": True}),
            FactoryRunStatus.DONE,
        ),
        (StepResult(ending=StepEnding.CLEAN, outcome="closed"), FactoryRunStatus.CANCELLED),
    ],
    ids=["merged", "closed"],
)
def test_closing_the_pull_request_wakes_the_run_which_ends_done_once_merged_or_else_cancelled(
    tmp_path, assessment, status
):
    control = FakeControl()
    control.step_results = [*CHECKED_CHANGE, OPENED, assessment]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)
    closed = json.dumps({"action": "closed", "pull_request": {"number": 42}}).encode()

    async def scenario() -> None:
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, line, closed, "pull_request")
        finished = await settled(hub, run.run_id)

        assert finished.run.status is status
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
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        for round_number in range(1, 4):
            await babysitting(hub, run.run_id, times=round_number)
            await woken(hub, runtime, line, REVIEWED, "pull_request_review")
        await babysitting(hub, run.run_id, times=4)
        await woken(hub, runtime, line, REVIEWED, "pull_request_review")
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
        assert report.folder == WORKTREE

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
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
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
        assert report.results == BRANCH
        assert report.hook is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("event", "body", "status"),
    [
        (
            "pull_request",
            {"action": "closed", "pull_request": {"number": 42, "merged": True}},
            FactoryRunStatus.DONE,
        ),
        (
            "pull_request",
            {"action": "closed", "pull_request": {"number": 42, "merged": False}},
            FactoryRunStatus.CANCELLED,
        ),
        ("issues", {"action": "closed", "issue": {"number": 7}}, FactoryRunStatus.CANCELLED),
    ],
    ids=["merged", "closed-unmerged", "issue-closed"],
)
def test_finished_work_ends_a_run_that_needs_human_and_makes_the_ended_call(
    tmp_path, event, body, status
):
    control = FakeControl()
    broken = StepResult(ending=StepEnding.FAILED, summary="GitHub is down.")
    control.step_results = [*CHECKED_CHANGE, OPENED, broken]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await babysitting(hub, run.run_id)
        await woken(hub, runtime, line, REVIEWED, "pull_request_review")
        stopped = await settled(hub, run.run_id)
        await woken(hub, runtime, line, json.dumps(body).encode(), event)
        async with asyncio.timeout(5):
            while not control.removed_folders:
                await asyncio.sleep(0.01)
        finished = await detail(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "assess")
        assert finished.run.status is status
        _, ended = control.steps[-1]
        assert ended.step == CodeStepRun(call="report_ended")
        assert ended.origin.run_id == run.run_id
        assert ended.results["pr"] == 42
        assert [command.folder for _, command in control.removed_folders] == [WORKTREE]

    asyncio.run(scenario())


#: Why the ticket needs no change, as the implement step records it.
NO_CHANGE_REASON = "Dark mode shipped in #6."


def test_an_implement_with_no_change_ends_the_run_cancelled_and_makes_the_ended_call(tmp_path):
    control = FakeControl()
    unchanged = StepResult(
        ending=StepEnding.CLEAN, outcome="no-change", values={"no_change": NO_CHANGE_REASON}
    )
    control.step_results = [VETTED, SET_UP, unchanged]
    runtime = FakeRuntime()
    hub = fresh_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            while len(control.steps) < 4:
                await asyncio.sleep(0.01)

        assert (finished.run.status, finished.run.step) == (
            FactoryRunStatus.CANCELLED,
            "implement",
        )
        _, ended = control.steps[-1]
        assert ended.step == CodeStepRun(call="report_ended")
        assert (ended.origin.run_id, ended.origin.step) == (run.run_id, "implement")
        assert ended.results == {**BRANCH, "no_change": NO_CHANGE_REASON}

    asyncio.run(scenario())


def with_review(factory: str) -> str:
    """The software factory's file with its review step uncommented."""
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
        VETTED,
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
        line = await software_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        waiting = await babysitting(hub, run.run_id)

        assert [attempt.step for attempt in waiting.attempts] == [
            "ticket",
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
            "listed = json.loads(answers.read_text())\n"
            "for index, (needles, output, error, once) in enumerate(listed):\n"
            "    if all(needle in arguments for needle in needles):\n"
            "        if once:\n"
            "            del listed[index]\n"
            "            answers.write_text(json.dumps(listed))\n"
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

    def answer(
        self, *needles: str, output: object = "", error: str | None = None, once: bool = False
    ) -> None:
        """Answer a call whose arguments hold every one of *needles*, the first answer that fits.

        An *error* fails the call with that message. A *once* answer answers one call only.
        """
        answers = json.loads(self._answers.read_text())
        text = output if isinstance(output, str) else json.dumps(output)
        answers.append([list(needles), text, error, once])
        self._answers.write_text(json.dumps(answers))

    def forget(self) -> None:
        """Drop every answer, once what GitHub holds has changed, and keep the calls."""
        self._answers.write_text("[]")

    @property
    def calls(self) -> list[list[str]]:
        if not self._calls.exists():
            return []
        return [json.loads(line) for line in self._calls.read_text().splitlines()]


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=directory, check=True, capture_output=True, text=True
    ).stdout.strip()


@dataclass(frozen=True)
class Bench:
    """A workbench a line of the software factory runs on, in its directory."""

    path: Path
    steps: WorkbenchSteps

    @property
    def workspace(self) -> Path:
        """Its workspace, a clone of the repository."""
        return self.path / "workspace"


def coder_at(tmp_path: Path) -> Bench:
    """A workbench of the software factory, its workspace a clone of origin, whose main holds
    one commit."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(("git", "init", "--bare", "-b", "main", str(origin)), check=True)
    subprocess.run(("git", "clone", str(origin), str(seed)), check=True, capture_output=True)
    commit(seed, "README.md", "The project.\n")
    git(seed, "push", "origin", "main")
    bench = Bench(
        tmp_path / "workbench",
        WorkbenchSteps(tmp_path / "workbench", tmp_path / "factories", SHIPPED_FACTORIES),
    )
    workspace = bench.workspace
    subprocess.run(("git", "clone", str(origin), str(workspace)), check=True, capture_output=True)
    git(workspace, "config", "user.name", "Software factory")
    git(workspace, "config", "user.email", "factory@example.com")
    return bench


def cut(bench: Bench, branch: str = BRANCH["branch"]) -> Path:
    """The run worktree of *branch*, cut fresh from origin's main as the workbench cuts it."""
    return cut_run_worktree(bench.path, bench.workspace, branch, "origin/main", str(uuid4()))


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


async def run_in(dispatcher: WorkbenchSteps, command: StepRunCommand) -> StepResult:
    """Run the step on the workbench in the folder the hub would name: the run worktree of the
    branch and base the run holds, or else a directory named for the run."""
    if command.folder is None:
        branch, base = command.results.get("branch"), command.results.get("base")
        folder = (
            RunFolder(branch=branch, base=base)
            if isinstance(branch, str) and isinstance(base, str)
            else RunFolder(branch=None)
        )
        command = command.model_copy(update={"folder": folder})
    return await dispatcher.run(command)


def origin(step: str) -> FactoryRunOrigin:
    return FactoryRunOrigin(factory="software", run_id=uuid4(), step=step)


#: What a pull request's body ends with when no adversarial review ran before it opened.
REVIEW_NOT_RUN = (
    "\n\n## Review status\n\nAdversarial review was not run. Review happens on this pull request."
)


async def reported(
    dispatcher: WorkbenchSteps,
    run: FactoryRunOrigin,
    summary: str,
    results: Mapping[str, StepValue] = BRANCH,
) -> StepResult:
    """Make the factory's needs_human call for issue 7's run, stopped at *run*'s step."""
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="report_needs_human", summary=summary),
            origin=run,
            work_item={"issue": 7},
            results=results,
        ),
    )


def answer_blocked_behind(github: FakeGitHub) -> None:
    """#8 and #11 are blocked by #7, and #9 by #8. Closed #10 is blocked by #7 and blocks #12."""
    closed = {**issue(10, "Drop the light theme"), "state": "closed"}
    for number, blocking in {
        7: [issue(8, "Add a theme picker"), closed, issue(11, "Theme the emails")],
        8: [issue(9, "Remember the chosen theme"), issue(11, "Theme the emails")],
        9: [],
        10: [issue(12, "Theme the docs")],
        11: [],
    }.items():
        github.answer(ISSUE.format(number=number) + "/dependencies/blocking", output=[blocking])


def stop_comments(github: FakeGitHub) -> list[str]:
    comments = [arguments for arguments in github.calls if arguments[:2] == ["issue", "comment"]]
    assert all(arguments[2:4] == ["7", "--body"] for arguments in comments)
    return [arguments[4] for arguments in comments]


def test_needs_human_pushes_a_branch_ahead_of_its_base_and_leaves_its_run_worktree_untouched(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_prepare(github, 7, "Add dark mode")
    answer_blocked_behind(github)
    dispatcher = bench.steps
    worktree = run_worktree(bench)

    async def scenario() -> None:
        await prepared(dispatcher, 7)
        commit(worktree, "theme.py", "DARK = True\n")
        (worktree / "theme.py").write_text("DARK = False\n")
        (worktree / "notes.md").write_text("Half done.\n")
        result = await reported(dispatcher, origin("implement"), "The session ran out of time.")

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
        assert len(stop_comments(github)) == 1
        pushed = git(workspace, "ls-remote", "origin", BRANCH["branch"]).split()
        assert pushed == [git(worktree, "rev-parse", "HEAD"), f"refs/heads/{BRANCH['branch']}"]
        assert git(worktree, "branch", "--show-current") == BRANCH["branch"]
        assert git(worktree, "log", "-1", "--format=%s") == "Write theme.py"
        assert git(worktree, "status", "--porcelain") == "M theme.py\n?? notes.md"
        assert (worktree / "theme.py").read_text() == "DARK = False\n"

    asyncio.run(scenario())


@pytest.mark.parametrize("results", [BRANCH, {}], ids=["branch-at-its-base", "before-a-branch"])
def test_needs_human_pushes_nothing_without_commits_ahead_of_the_base(
    tmp_path, monkeypatch, results
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    git(workspace, "switch", "-c", BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_blocked_behind(github)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await reported(dispatcher, origin("ticket"), "GitHub is down.", results)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert len(stop_comments(github)) == 1
        assert git(workspace, "ls-remote", "--heads", "origin").endswith("refs/heads/main")
        assert len(git(workspace, "ls-remote", "--heads", "origin").splitlines()) == 1

    asyncio.run(scenario())


def test_needs_human_pushes_a_branch_whose_merged_base_is_gone_from_origin(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    stacked_on_a_merged_blocker(bench, github)
    answer_blocked_behind(github)
    dispatcher = bench.steps

    async def scenario() -> None:
        stacked = {"branch": BRANCH["branch"], "base": "agent/6-step"}
        result = await reported(dispatcher, origin("implement"), "Checks failed.", stacked)

        assert result.ending is StepEnding.CLEAN, result.summary
        [stopped] = stop_comments(github)
        assert "Pushing" not in stopped
        pushed = git(workspace, "ls-remote", "origin", BRANCH["branch"]).split()
        head = git(workspace, "rev-parse", BRANCH["branch"])
        assert pushed == [head, f"refs/heads/{BRANCH['branch']}"]

    asyncio.run(scenario())


def test_needs_human_reports_a_push_that_failed_in_its_comment(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    git(workspace, "switch", "-c", BRANCH["branch"])
    commit(workspace, "theme.py", "DARK = True\n")
    git(workspace, "remote", "set-url", "--push", "origin", str(tmp_path / "gone.git"))
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_blocked_behind(github)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await reported(dispatcher, origin("publish"), "GitHub is down.")

        assert result.ending is StepEnding.CLEAN, result.summary
        [stopped] = stop_comments(github)
        assert f"\n\nPushing `{BRANCH['branch']}` failed: git push exited with code" in stopped
        assert any(arguments[:3] == ["issue", "edit", "7"] for arguments in github.calls)

    asyncio.run(scenario())


def test_each_stop_of_a_run_comments_its_step_cause_run_id_retry_and_the_issues_blocked_behind(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    git(workspace, "switch", "-c", BRANCH["branch"])
    commit(workspace, "theme.py", "DARK = True\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_blocked_behind(github)
    dispatcher = bench.steps
    run = origin("implement")

    async def scenario() -> None:
        await reported(dispatcher, run, "The session ran out of time.")
        commit(run_worktree(bench), "picker.py", "PICKER = True\n")
        retried = await reported(
            dispatcher,
            run.model_copy(update={"step": "checks"}),
            "`bun run check` exited with code 1.",
        )

        assert retried.ending is StepEnding.CLEAN, retried.summary
        assert stop_comments(github) == [
            f"The software factory stopped at step `{step}`: {cause}\n\n"
            f"Run `{run.run_id}`. Add `ready-for-agent` to retry this step; send back or cancel "
            "from the web app or `hub.sh`.\n\n"
            "Blocked behind this issue:\n"
            "- #8 Add a theme picker\n"
            "- #11 Theme the emails\n"
            "- #9 Remember the chosen theme"
            for step, cause in [
                ("implement", "The session ran out of time."),
                ("checks", "`bun run check` exited with code 1."),
            ]
        ]
        pushed = git(workspace, "ls-remote", "origin", BRANCH["branch"]).split()[0]
        assert pushed == git(run_worktree(bench), "rev-parse", "HEAD")

    asyncio.run(scenario())


async def ended(
    dispatcher: WorkbenchSteps,
    results: Mapping[str, StepValue],
    run: FactoryRunOrigin | None = None,
    number: int = 7,
) -> StepResult:
    """Make the factory's ended call for issue *number*'s *run*, which recorded *results*."""
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="report_ended"),
            origin=run or origin("implement"),
            work_item={"issue": number},
            results=results,
        ),
    )


#: What the ended call writes to take ready-for-human off issue 7.
HUMAN_LABEL_OFF = ["issue", "edit", "7", "--remove-label", "ready-for-human"]


@pytest.mark.parametrize(
    ("results", "labels", "calls"),
    [
        (
            {**BRANCH, "no_change": NO_CHANGE_REASON},
            ["ready-for-agent"],
            [
                ["issue", "edit", "7", "--remove-label", "ready-for-agent"],
                [
                    "issue",
                    "comment",
                    "7",
                    "--body",
                    f"The software factory made no change for this issue: {NO_CHANGE_REASON}\n\n"
                    "Close it if you agree, or add `ready-for-agent` to try again.",
                ],
            ],
        ),
        ({**BRANCH, "pr": 42, "merge_ready": True}, ["ready-for-agent"], []),
        ({}, ["ready-for-agent"], []),
        ({**BRANCH, "pr": 42}, ["ready-for-human"], [HUMAN_LABEL_OFF]),
        ({}, ["ready-for-human"], [HUMAN_LABEL_OFF]),
    ],
    ids=[
        "no-change",
        "merge-ready",
        "before-a-branch",
        "after-needing-a-human",
        "needing-a-human-before-a-branch",
    ],
)
def test_the_ended_call_takes_ready_for_human_off_and_says_why_a_run_made_no_change(
    tmp_path, monkeypatch, results, labels, calls
):
    bench = coder_at(tmp_path)
    run = origin("implement")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode", labels=labels))
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await ended(dispatcher, results, run)

        assert result.ending is StepEnding.CLEAN, result.summary
        # The issue is never closed: only these writes reach it.
        assert [arguments for arguments in github.calls if arguments[0] == "issue"] == calls

    asyncio.run(scenario())


#: What the stub client runs to commit its work on the branch it is on.
COMMITS = (
    "import subprocess\n"
    "open('theme.py', 'w').write('DARK = True\\n')\n"
    "subprocess.run(['git', 'add', 'theme.py'], check=True, capture_output=True)\n"
    "subprocess.run(['git', 'commit', '-m', 'Add dark mode'], check=True, capture_output=True)\n"
)
LEAVES_CHANGES = "open('notes.md', 'w').write('Half done.\\n')\n"
#: What the stub client runs to write its pull request's title and body.
WRITES_WORDS = (
    "import os\n"
    "os.makedirs('.scratch', exist_ok=True)\n"
    "open('.scratch/pr-title.txt', 'w').write('Add a dark mode toggle\\n')\n"
    "open('.scratch/pr-body.md', 'w').write('Adds dark mode.\\n')\n"
)
WORDS = {"title": "Add a dark mode toggle", "body": "Adds dark mode."}
MALFORMED = 'print(\'{"type": "result", "subt\')\n'


@pytest.mark.parametrize(
    ("then", "ending", "reason", "words"),
    [
        (COMMITS + streaming(CLAUDE_STREAM), StepEnding.CLEAN, None, {}),
        (COMMITS + MALFORMED, StepEnding.CLEAN, None, {}),
        (COMMITS + WRITES_WORDS + streaming(CLAUDE_STREAM), StepEnding.CLEAN, None, WORDS),
        (
            streaming(CLAUDE_STREAM),
            StepEnding.FAILED,
            "Branch agent/7-add-dark-mode has no commits ahead of main.",
            {},
        ),
        (
            WRITES_WORDS + streaming(CLAUDE_STREAM),
            StepEnding.FAILED,
            "Branch agent/7-add-dark-mode has no commits ahead of main.",
            {},
        ),
        (
            COMMITS + LEAVES_CHANGES + streaming(CLAUDE_STREAM),
            StepEnding.FAILED,
            "The workspace has changes no commit on agent/7-add-dark-mode holds:\n?? notes.md",
            {},
        ),
    ],
    ids=[
        "committed",
        "committed-malformed-stream",
        "committed-with-words",
        "no-commits",
        "words-but-no-commits",
        "dirty-tree",
    ],
)
def test_the_implement_hook_passes_only_committed_work_and_records_the_words_the_client_wrote(
    tmp_path, monkeypatch, then, ending, reason, words
):
    bench = coder_at(tmp_path)
    workspace = cut(bench)
    stub_client(tmp_path, monkeypatch, "claude", then)
    dispatcher = bench.steps

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
        assert result.values == words
        # Another run's step must never find them, even when this step failed.
        assert not any((workspace / ".scratch").glob("pr-*"))
        if reason is not None:
            assert result.summary.endswith(
                f'Check "check_implementation" failed: UncommittedWork: {reason}'
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("moved", "ending"),
    [(False, StepEnding.CLEAN), (True, StepEnding.FAILED)],
    ids=["commit-past-the-blocker", "moved-onto-main-with-no-commit"],
)
def test_the_implement_hook_counts_commits_past_a_merged_base_gone_from_origin(
    tmp_path, monkeypatch, moved, ending
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    stacked_on_a_merged_blocker(bench, github)
    if moved:
        git(workspace, "reset", "--hard", "origin/main")
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await run_in(
            dispatcher,
            StepRunCommand(
                step=CommandStepRun(run=["true"]),
                hook="check_implementation",
                origin=origin("implement"),
                work_item={"issue": 7},
                results={"branch": BRANCH["branch"], "base": "agent/6-step"},
            ),
        )

        assert result.ending is ending, result.summary
        if moved:
            assert result.summary.endswith(
                f"UncommittedWork: Branch {BRANCH['branch']} has no commits ahead of agent/6-step."
            )

    asyncio.run(scenario())


def test_the_implement_hook_names_a_few_uncommitted_paths_and_counts_the_rest(tmp_path):
    bench = coder_at(tmp_path)
    workspace = cut(bench)
    commit(workspace, "theme.py", "DARK = True\n")
    store = workspace / ".pnpm-store"
    store.mkdir()
    for number in range(3000):
        (store / f"package-{number:04}.tgz").write_text("")
    for number in range(30):
        (workspace / f"loose-{number:02}.md").write_text("")
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await run_in(
            dispatcher,
            StepRunCommand(
                step=CommandStepRun(run=["true"]),
                hook="check_implementation",
                origin=origin("implement"),
                work_item={"issue": 7},
                results=BRANCH,
            ),
        )

        assert result.ending is StepEnding.FAILED
        assert len(result.summary) < 2_000
        assert "?? .pnpm-store/\n" in result.summary
        assert "?? loose-00.md" in result.summary
        assert result.summary.endswith("And 11 more.")

    asyncio.run(scenario())


def test_the_implement_hook_fails_on_an_untracked_file_git_is_set_to_hide(tmp_path):
    bench = coder_at(tmp_path)
    workspace = cut(bench)
    commit(workspace, "theme.py", "DARK = True\n")
    git(workspace, "config", "status.showUntrackedFiles", "no")
    (workspace / "notes.md").write_text("Half done.\n")
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await run_in(
            dispatcher,
            StepRunCommand(
                step=CommandStepRun(run=["true"]),
                hook="check_implementation",
                origin=origin("implement"),
                work_item={"issue": 7},
                results=BRANCH,
            ),
        )

        assert result.ending is StepEnding.FAILED
        assert result.summary.endswith(
            "UncommittedWork: The workspace has changes no commit on agent/7-add-dark-mode "
            "holds:\n?? notes.md"
        )

    asyncio.run(scenario())


#: What the stub client runs to record that the ticket needs no change, and why.
WRITES_NO_CHANGE = (
    "import os\n"
    "os.makedirs('.scratch', exist_ok=True)\n"
    f"open('.scratch/no-change.md', 'w').write({NO_CHANGE_REASON + chr(10)!r})\n"
)


@pytest.mark.parametrize(
    ("then", "ending", "outcome", "values", "reason"),
    [
        (
            WRITES_NO_CHANGE + streaming(CLAUDE_STREAM),
            StepEnding.CLEAN,
            "no-change",
            {"no_change": NO_CHANGE_REASON},
            None,
        ),
        (
            WRITES_NO_CHANGE + LEAVES_CHANGES + streaming(CLAUDE_STREAM),
            StepEnding.FAILED,
            None,
            {},
            "UncommittedWork: The workspace has changes no commit on agent/7-add-dark-mode "
            "holds:\n?? notes.md",
        ),
        (
            COMMITS + WRITES_NO_CHANGE + streaming(CLAUDE_STREAM),
            StepEnding.FAILED,
            None,
            {},
            "ChangeAndNoChange: .scratch/no-change.md says the ticket needs no change, but "
            "branch agent/7-add-dark-mode has commits ahead of main.",
        ),
    ],
    ids=["no-change", "no-change-with-an-untracked-file", "no-change-with-commits"],
)
def test_the_implement_hook_passes_a_branch_with_no_commits_only_under_no_change(
    tmp_path, monkeypatch, then, ending, outcome, values, reason
):
    bench = coder_at(tmp_path)
    workspace = cut(bench)
    stub_client(tmp_path, monkeypatch, "claude", then)
    dispatcher = bench.steps

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

        assert (result.ending, result.outcome, result.values) == (ending, outcome, values)
        assert not (workspace / ".scratch" / "no-change.md").exists()
        if reason is not None:
            assert result.summary.endswith(reason)

    asyncio.run(scenario())


def run_worktree(bench: Bench, branch: str = BRANCH["branch"]) -> Path:
    return bench.path / "worktrees" / branch


def answer_prepare(github: FakeGitHub, number: int, title: str) -> None:
    """Answer prepare's calls for issue *number*, which no open issue blocks."""
    answer_issue(github, issue(number, title))
    github.answer(ISSUE.format(number=number) + "/dependencies/blocked_by", output=blockers())
    github.answer("repo", "view", output=REPOSITORY)


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
        "user": {"login": author, "type": "User"},
        "author_association": association,
        "parent_issue_url": (
            None
            if parent is None
            else f"https://api.github.com/repos/owner/project/issues/{parent}"
        ),
    }


def comment(author: str, association: str, kind: str = "User") -> dict[str, object]:
    return {
        "body": "A note.",
        "user": {"login": author, "type": kind},
        "author_association": association,
    }


def pull_request(
    number: int,
    branch: str,
    closes: int,
    stack: int | None = None,
    *,
    base: str = "main",
    head_repository: str | None = "owner/project",
    requested: Sequence[str] = (),
) -> dict[str, object]:
    """An open agent pull request on *base*, as GitHub's REST API lists it, asking *requested*
    for reviews.

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
        "base": {"ref": base, "repo": {"full_name": "owner/project"}},
        "body": f"Closes #{closes}\n\nWhat changed.",
        "stack": None if stack is None else {"number": stack},
        "requested_reviewers": [{"login": login} for login in requested],
    }


def answer_issue(
    github: FakeGitHub, found: dict[str, object], comments: Sequence[dict[str, object]] = ()
) -> None:
    number = found["number"]
    github.answer(ISSUE.format(number=number) + "/comments", output=[list(comments)])
    github.answer(ISSUE.format(number=number), output=found)


async def prepared(
    dispatcher: WorkbenchSteps,
    number: int,
    run: FactoryRunOrigin | None = None,
    *,
    fresh: bool = True,
) -> StepResult:
    """Run the first step of issue *number*'s run, as the hub sends it: in a run worktree the
    factory's worktree call names, cut fresh from its base. A retry of a step whose folder was
    named sends no *fresh*."""
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="vet_ticket"),
            origin=run or origin("ticket"),
            work_item={"issue": number},
            folder=NAMED,
            fresh_folder=fresh,
        ),
    )


def test_the_first_step_works_in_the_agent_branch_cut_from_the_default_branch_scratch_excluded(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode!"), [comment("helper", "COLLABORATOR")])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer(ISSUE.format(number=7) + "/dependencies/blocked_by", output=blockers())
    dispatcher = bench.steps
    worktree = run_worktree(bench)

    async def scenario() -> None:
        result = await prepared(dispatcher, 7)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"branch": "agent/7-add-dark-mode", "base": "main"}
        assert git(worktree, "branch", "--show-current") == "agent/7-add-dark-mode"
        assert git(worktree, "rev-parse", "HEAD") == git(workspace, "rev-parse", "origin/main")
        assert git(worktree, "status", "--porcelain", "--untracked-files=all") == ""
        (worktree / ".scratch").mkdir()
        (worktree / ".scratch" / "pr-body.md").write_text("This run's body.\n")
        assert git(worktree, "status", "--porcelain", "--untracked-files=all") == ""
        assert git(workspace, "branch", "--show-current") == "main"

    asyncio.run(scenario())


def test_a_new_run_starts_from_the_base_whatever_an_abandoned_run_left_on_its_branch(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    abandoned = cut(bench)
    commit(abandoned, "theme.py", "DARK = None\n")
    git(abandoned, "push", "origin", BRANCH["branch"])
    git(bench.workspace, "worktree", "remove", "--force", str(abandoned))
    git(bench.workspace, "branch", "-D", BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_prepare(github, 7, "Add dark mode")

    result = asyncio.run(prepared(bench.steps, 7))

    worktree = run_worktree(bench)
    assert result.values == BRANCH
    assert git(worktree, "rev-parse", "HEAD") == git(bench.workspace, "rev-parse", "origin/main")
    assert not (worktree / "theme.py").exists()


def test_a_run_sent_back_to_its_first_step_starts_over_from_the_base_named_again(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    remote = tmp_path / "origin.git"
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_prepare(github, 7, "Add dark mode")
    dispatcher = bench.steps
    worktree = run_worktree(bench)
    run = origin("ticket")

    async def scenario() -> None:
        await prepared(dispatcher, 7, run)
        commit(worktree, "theme.py", "DARK = True\n")
        git(worktree, "push", "origin", BRANCH["branch"])
        (worktree / "notes.md").write_text("Half done.\n")
        sent_back = await prepared(dispatcher, 7, run, fresh=True)

        assert sent_back.values == BRANCH
        assert git(worktree, "rev-parse", "HEAD") == git(workspace, "rev-parse", "origin/main")
        assert git(worktree, "status", "--porcelain", "--untracked-files=all") == ""
        assert git(remote, "rev-parse", BRANCH["branch"]) != git(worktree, "rev-parse", "HEAD")

    asyncio.run(scenario())


@pytest.mark.parametrize("written_by", ["issue", "comment", "parent"])
def test_the_first_step_fails_on_ticket_text_an_untrusted_author_wrote(
    tmp_path, monkeypatch, written_by
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
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
    github.answer(ISSUE.format(number=7) + "/dependencies/blocked_by", output=blockers())
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await prepared(dispatcher, 7)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "vet_ticket" failed: UntrustedTicket: '
            "Ticket text from untrusted authors needs a human: stranger."
        )
        assert git(workspace, "branch", "--show-current") == "main"

    asyncio.run(scenario())


def test_the_first_step_trusts_a_comment_linears_github_app_wrote(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"), [comment("linear-code[bot]", "NONE", "Bot")])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer(ISSUE.format(number=7) + "/dependencies/blocked_by", output=blockers())
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await prepared(dispatcher, 7)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"branch": "agent/7-add-dark-mode", "base": "main"}

    asyncio.run(scenario())


@pytest.mark.parametrize(("author", "kind"), [("dependabot[bot]", "Bot"), ("linear-code", "User")])
def test_the_first_step_fails_on_a_comment_by_another_bot_or_a_user_named_like_linears_app(
    tmp_path, monkeypatch, author, kind
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"), [comment(author, "NONE", kind)])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer(ISSUE.format(number=7) + "/dependencies/blocked_by", output=blockers())
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await prepared(dispatcher, 7)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "vet_ticket" failed: UntrustedTicket: '
            f"Ticket text from untrusted authors needs a human: {author}."
        )

    asyncio.run(scenario())


def pushed(workspace: Path, number: int, start: str = "main") -> str:
    """Push issue *number*'s agent branch to origin, one commit on *start*, and name it."""
    branch = f"agent/{number}-step"
    git(workspace, "switch", "-c", branch, f"origin/{start}")
    commit(workspace, f"step{number}.py", f"STEP = {number}\n")
    git(workspace, "push", "origin", branch)
    git(workspace, "switch", "main")
    return branch


def answer_sub_issue(
    github: FakeGitHub, number: int, blocking: Sequence[int], *pulled: int
) -> None:
    """Answer for sub-issue *number* of #5, blocked by the issues *blocking*, among siblings whose
    agent pull requests close *pulled*, oldest first."""
    answer_issue(github, issue(number, "Dark theme", parent=5))
    answer_issue(github, issue(5, "Themes"))
    github.answer(
        ISSUE.format(number=number) + "/dependencies/blocked_by", output=blockers(*blocking)
    )
    siblings = sorted({*pulled, *blocking, number})
    github.answer(
        "repos/{owner}/{repo}/issues",
        output=[[issue(sibling, f"Step {sibling}", parent=5) for sibling in siblings]],
    )
    pulls = [pull_request(40 + closes, f"agent/{closes}-step", closes) for closes in pulled]
    github.answer("repos/{owner}/{repo}/pulls", output=[[*pulls[::-1], pull_request(30, "fix", 3)]])
    github.answer("repo", "view", output=REPOSITORY)


def test_a_ticket_whose_blocker_has_an_open_agent_pull_request_starts_from_its_branch(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    pushed(bench.workspace, 7)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(8, "Dark theme"))
    github.answer(ISSUE.format(number=8) + "/dependencies/blocked_by", output=blockers(7))
    github.answer("repos/{owner}/{repo}/pulls", output=[[pull_request(47, "agent/7-step", 7)]])
    github.answer("repo", "view", output=REPOSITORY)

    result = asyncio.run(prepared(bench.steps, 8))

    assert result.ending is StepEnding.CLEAN, result.summary
    assert result.values == {"branch": "agent/8-dark-theme", "base": "agent/7-step"}
    worktree = run_worktree(bench, "agent/8-dark-theme")
    assert git(worktree, "log", "-1", "--format=%s") == "Write step7.py"


def test_the_worktree_call_starts_a_sub_issue_with_no_open_blocker_from_the_default_branch(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    pushed(workspace, 7)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_sub_issue(github, 8, (), 7)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await prepared(dispatcher, 8)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"branch": "agent/8-dark-theme", "base": "main"}
        assert git(run_worktree(bench, "agent/8-dark-theme"), "rev-parse", "HEAD") == git(
            workspace, "rev-parse", "origin/main"
        )

    asyncio.run(scenario())


def test_the_worktree_call_stacks_a_sub_issue_on_the_top_of_its_blockers_linear_stack(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    pushed(workspace, 8, pushed(workspace, 7))
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_sub_issue(github, 9, (7, 8), 7, 8)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await prepared(dispatcher, 9)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"branch": "agent/9-dark-theme", "base": "agent/8-step"}
        assert (
            git(run_worktree(bench, "agent/9-dark-theme"), "log", "--format=%s")
            == "Write step8.py\nWrite step7.py\nWrite README.md"
        )

    asyncio.run(scenario())


def forked(workspace: Path) -> None:
    """The stack #771 → #774 → #775 and #771 → #772 → #773 forked into, as 1 → 4 → 5 and
    1 → 2 → 3."""
    first = pushed(workspace, 1)
    pushed(workspace, 5, pushed(workspace, 4, first))
    pushed(workspace, 3, pushed(workspace, 2, first))


def test_the_worktree_call_hands_a_human_blockers_on_a_forked_stack_no_branch_holds(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    forked(workspace)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_sub_issue(github, 6, (3, 4), 1, 4, 5, 2, 3)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await prepared(dispatcher, 6)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "agent_branch" failed: DivergedBlockers: No agent branch holds every open '
            "blocker's agent branch, so a human picks the base: agent/3-step, agent/4-step."
        )

    asyncio.run(scenario())


def test_the_worktree_call_stacks_on_a_sibling_branch_that_holds_a_forked_stacks_blockers(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    forked(workspace)
    git(workspace, "switch", "-c", "agent/7-step", "origin/agent/3-step")
    git(workspace, "merge", "--no-edit", "origin/agent/4-step")
    git(workspace, "push", "origin", "agent/7-step")
    git(workspace, "switch", "main")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_sub_issue(github, 6, (3, 4), 1, 4, 5, 2, 3, 7)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await prepared(dispatcher, 6)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"branch": "agent/6-dark-theme", "base": "agent/7-step"}

    asyncio.run(scenario())


def implemented(bench: Bench, branch: str, base: str = "main") -> Path:
    """The run worktree of *branch* as the implement step leaves it: a commit on origin's main,
    its base on origin."""
    if base != "main":
        git(bench.workspace, "push", "origin", f"origin/main:refs/heads/{base}")
    worktree = cut(bench, branch)
    commit(worktree, "theme.py", "DARK = True\n")
    return worktree


#: The body the implement step recorded for its pull request.
BODY = {"body": "Adds dark mode."}


async def opened(
    dispatcher: WorkbenchSteps, number: int, results: Mapping[str, StepValue]
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
    bench = coder_at(tmp_path)
    workspace = implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        closing = {"body": "Closes #7\n\nAdds dark mode."}
        result = await opened(dispatcher, 7, BRANCH | closing | reviewed)

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


def test_publish_pushes_a_branch_deleted_from_origin_after_the_workspace_fetched_it(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    remote = tmp_path / "origin.git"
    git(workspace, "push", "origin", f"origin/main:refs/heads/{BRANCH['branch']}")
    git(workspace, "fetch", "origin")
    git(remote, "branch", "-D", BRANCH["branch"])
    worktree = implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert git(remote, "rev-parse", BRANCH["branch"]) == git(worktree, "rev-parse", "HEAD")

    asyncio.run(scenario())


def test_publish_never_pushes_over_a_branch_someone_else_moved_on_origin(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    remote = tmp_path / "origin.git"
    git(workspace, "push", "origin", f"origin/main:refs/heads/{BRANCH['branch']}")
    git(workspace, "fetch", "origin")
    other = tmp_path / "other"
    subprocess.run(("git", "clone", str(remote), str(other)), check=True, capture_output=True)
    git(other, "switch", BRANCH["branch"])
    commit(other, "other.py", "OTHER = True\n")
    git(other, "push", "origin", BRANCH["branch"])
    theirs = git(other, "rev-parse", "HEAD")
    implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | BODY)

        assert result.ending is StepEnding.FAILED
        assert "stale info" in result.summary
        assert git(remote, "rev-parse", BRANCH["branch"]) == theirs

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
    bench = coder_at(tmp_path)
    implemented(bench, "agent/8-dark-theme", "agent/7-light-theme")
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
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(
            dispatcher,
            8,
            {
                "branch": "agent/8-dark-theme",
                "base": "agent/7-light-theme",
                "body": "Adds the dark theme.",
            },
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


def test_publish_stacks_on_a_blocker_whose_issue_was_handed_to_a_human(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    implemented(bench, "agent/8-dark-theme", "agent/7-light-theme")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(8, "Dark theme", parent=5))
    github.answer(
        "repos/{owner}/{repo}/pulls",
        output=[[pull_request(41, "agent/7-light-theme", closes=7, stack=3)]],
    )
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        branch = {"branch": "agent/8-dark-theme", "base": "agent/7-light-theme"}
        result = await opened(dispatcher, 8, branch | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        [stacked] = [arguments for arguments in github.calls if "POST" in arguments]
        assert "repos/{owner}/{repo}/stacks/3/add" in stacked

    asyncio.run(scenario())


def test_publish_stacks_no_sub_issues_pull_request_based_on_the_default_branch(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    implemented(bench, "agent/8-dark-theme")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(8, "Dark theme", parent=5))
    github.answer(
        "repos/{owner}/{repo}/issues",
        output=[[issue(7, "Light theme", parent=5), issue(8, "Dark theme", parent=5)]],
    )
    github.answer(
        "repos/{owner}/{repo}/pulls",
        output=[[pull_request(41, "agent/7-light-theme", closes=7, stack=3)]],
    )
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        branch = {"branch": "agent/8-dark-theme", "base": "main"}
        result = await opened(dispatcher, 8, branch | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert not any("POST" in arguments for arguments in github.calls)

    asyncio.run(scenario())


def stacked_on_a_merged_blocker(bench: Bench, github: FakeGitHub, *on_main: str) -> None:
    """The run's branch, one commit on blocker 6's branch, once 6's pull request squash-merged
    into main and took its branch from origin. Main also holds a theme.py of each of *on_main*.
    GitHub answers with the merged pull request."""
    workspace = bench.workspace
    blocker = pushed(workspace, 6)
    head = git(workspace, "rev-parse", f"origin/{blocker}")
    git(workspace, "switch", "-c", BRANCH["branch"], f"origin/{blocker}")
    commit(workspace, "theme.py", "DARK = True\n")
    git(workspace, "switch", "--detach", "origin/main")
    commit(workspace, "step6.py", "STEP = 6\n")
    for content in on_main:
        commit(workspace, "theme.py", content)
    git(workspace, "push", "origin", "HEAD:main", f":{blocker}")
    git(workspace, "switch", BRANCH["branch"])
    # GitHub serves a merged pull request's head after its branch is gone.
    origin = git(workspace, "remote", "get-url", "origin")
    git(Path(origin), "config", "uploadpack.allowAnySHA1InWant", "true")
    merged = [{"headRefOid": head, "baseRefName": "main"}]
    github.answer("pr", "list", "--state", "merged", "--head", blocker, output=merged)


def test_publish_opens_on_where_a_merged_base_landed_with_only_the_runs_commits(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    stacked_on_a_merged_blocker(bench, github)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        stacked = {"branch": BRANCH["branch"], "base": "agent/6-step"}
        result = await opened(dispatcher, 7, stacked | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert created_pull_request(github)[4:6] == ["--base", "main"]
        origin = tmp_path / "origin.git"
        assert git(origin, "rev-parse", f"{BRANCH['branch']}^") == git(origin, "rev-parse", "main")

    asyncio.run(scenario())


def test_publish_hands_a_human_a_branch_that_conflicts_where_its_merged_base_landed(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    stacked_on_a_merged_blocker(bench, github, "DARK = False\n")
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher = bench.steps

    async def scenario() -> None:
        stacked = {"branch": BRANCH["branch"], "base": "agent/6-step"}
        result = await opened(dispatcher, 7, stacked | BODY)

        assert result.ending is StepEnding.FAILED
        assert result.summary.startswith(
            'Tool "publish_pull_request" failed: LostBase: '
            f"Branch {BRANCH['branch']} does not rebase onto main:"
        )
        worktree = bench.path / "worktrees" / BRANCH["branch"]
        assert git(worktree, "status", "--porcelain") == ""
        assert git(worktree, "rev-parse", "HEAD^") != git(worktree, "rev-parse", "origin/main")
        assert not any(arguments[:2] == ["pr", "create"] for arguments in github.calls)

    asyncio.run(scenario())


def test_publish_replays_only_the_runs_commits_of_a_branch_moved_onto_where_its_base_landed(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = bench.workspace
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    # Main writes theme.py twice, so replaying main's own commits on main would conflict.
    stacked_on_a_merged_blocker(bench, github, "LIGHT = True\n", "LIGHT = False\n")
    git(workspace, "reset", "--hard", "origin/main")
    commit(workspace, "report.py", "REPORT = True\n")
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        stacked = {"branch": BRANCH["branch"], "base": "agent/6-step"}
        result = await opened(dispatcher, 7, stacked | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert created_pull_request(github)[4:6] == ["--base", "main"]
        origin = tmp_path / "origin.git"
        assert git(origin, "rev-parse", f"{BRANCH['branch']}^") == git(origin, "rev-parse", "main")

    asyncio.run(scenario())


def test_publish_hands_a_human_a_base_gone_with_no_merged_pull_request(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("pr", "list", "--state", "merged", output=[])
    dispatcher = bench.steps

    async def scenario() -> None:
        stacked = {"branch": BRANCH["branch"], "base": "agent/6-step"}
        result = await opened(dispatcher, 7, stacked | BODY)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "publish_pull_request" failed: LostBase: '
            "Branch agent/6-step is gone from origin, and no pull request of it merged."
        )
        assert git(tmp_path / "origin.git", "branch", "--list", BRANCH["branch"]) == ""

    asyncio.run(scenario())


def test_publish_takes_ready_for_human_off_an_issue_an_earlier_failure_handed_to_a_human(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode", labels=["ready-for-human"]))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert github.calls[-1] == ["issue", "edit", "7", "--remove-label", "ready-for-human"]

    asyncio.run(scenario())


def test_publish_titles_the_pull_request_with_the_title_the_session_wrote(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | WORDS)

        assert result.ending is StepEnding.CLEAN, result.summary
        created = created_pull_request(github)
        assert created[created.index("--title") + 1] == "Add a dark mode toggle"

    asyncio.run(scenario())


def test_publish_run_twice_opens_one_pull_request_and_updates_it_the_second_time(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    workspace = implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repos/{owner}/{repo}/pulls", output=[[]])
    github.answer("repo", "view", output=REPOSITORY)
    github.answer("pr", "create", output="https://github.com/owner/project/pull/43")
    dispatcher = bench.steps

    async def scenario() -> None:
        first = await opened(dispatcher, 7, BRANCH | BODY)
        github.forget()
        answer_issue(github, issue(7, "Add dark mode"))
        github.answer(
            "repos/{owner}/{repo}/pulls",
            output=[[pull_request(43, BRANCH["branch"], closes=7, requested=["owner"])]],
        )
        github.answer("repo", "view", output=REPOSITORY)
        second = await opened(dispatcher, 7, BRANCH | BODY)

        assert second.ending is StepEnding.CLEAN, second.summary
        assert first.values == second.values == {"pr": 43}
        assert created_pull_request(github)
        edited = [arguments for arguments in github.calls if arguments[:2] == ["pr", "edit"]]
        assert edited == [
            [
                "pr",
                "edit",
                "43",
                "--title",
                "Add dark mode",
                "--body",
                f"Closes #7\n\nAdds dark mode.{REVIEW_NOT_RUN}",
            ]
        ]
        assert git(tmp_path / "origin.git", "rev-parse", BRANCH["branch"]) == git(
            workspace, "rev-parse", "HEAD"
        )

    asyncio.run(scenario())


def test_publish_run_again_asks_the_maintainer_for_a_review_an_earlier_run_never_asked_for(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    implemented(bench, BRANCH["branch"])
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer(
        "repos/{owner}/{repo}/pulls", output=[[pull_request(43, BRANCH["branch"], closes=7)]]
    )
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        [edited] = [arguments for arguments in github.calls if arguments[:2] == ["pr", "edit"]]
        assert edited[-2:] == ["--add-reviewer", "owner"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("stack", "base", "rebased", "stacks"),
    [
        (3, "main", [], 0),
        (None, "agent/7-light-theme", [], 1),
        (None, "main", ["--base", "agent/7-light-theme"], 1),
    ],
    ids=["stacked", "unstacked-on-its-base", "unstacked-elsewhere"],
)
def test_publish_run_again_moves_and_stacks_its_pull_request_only_when_it_is_in_no_stack(
    tmp_path, monkeypatch, stack, base, rebased, stacks
):
    bench = coder_at(tmp_path)
    implemented(bench, "agent/8-dark-theme", "agent/7-light-theme")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(8, "Dark theme", parent=5))
    github.answer(
        "repos/{owner}/{repo}/pulls",
        output=[
            [
                pull_request(43, "agent/8-dark-theme", closes=8, stack=stack, base=base),
                pull_request(41, "agent/7-light-theme", closes=7, stack=3),
            ]
        ],
    )
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher = bench.steps

    async def scenario() -> None:
        branch = {"branch": "agent/8-dark-theme", "base": "agent/7-light-theme"}
        result = await opened(dispatcher, 8, branch | BODY)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.values == {"pr": 43}
        assert not any(arguments[:2] == ["pr", "create"] for arguments in github.calls)
        edited = [arguments for arguments in github.calls if arguments[:2] == ["pr", "edit"]]
        assert edited == [
            [
                *("pr", "edit", "43", *rebased),
                *("--title", "Dark theme"),
                *("--body", f"Closes #8\n\nAdds dark mode.{REVIEW_NOT_RUN}"),
                *("--add-reviewer", "owner"),
            ]
        ]
        assert sum("POST" in arguments for arguments in github.calls) == stacks

    asyncio.run(scenario())


def test_publish_pushes_nothing_when_the_session_wrote_no_pull_request_body(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    workspace = implemented(bench, BRANCH["branch"])
    # A body left in the run worktree, which publish never reads.
    (workspace / ".scratch").mkdir()
    (workspace / ".scratch" / "pr-body.md").write_text("Adds a light mode.\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_issue(github, issue(7, "Add dark mode"))
    github.answer("repo", "view", output=REPOSITORY)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "publish_pull_request" failed: MissingPullRequestBody: '
            "The coding session wrote no pull request body."
        )
        assert not any(arguments[:2] == ["pr", "create"] for arguments in github.calls)
        assert git(tmp_path / "origin.git", "branch", "--list", BRANCH["branch"]) == ""

    asyncio.run(scenario())


def blockers(*numbers: int) -> list[list[dict[str, object]]]:
    """The open issues that block one, as GitHub's dependencies API lists them."""
    return [[{"number": number, "state": "open", "parent_issue_url": None} for number in numbers]]


async def scanned(
    dispatcher: WorkbenchSteps, delivery: Mapping[str, object] | None = None
) -> list[dict[str, StepValue]]:
    """Run the factory's intake on the workbench, with *delivery* as its signal's body, and the
    work items it finds."""
    signal = {} if delivery is None else {"body": dict(delivery)}
    found = await dispatcher.intake(
        IntakeRunCommand(factory="software", call="scan_ready_issues", signal=signal)
    )
    return found.work_items


def test_the_scan_finds_the_oldest_eligible_issue(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    github.answer(
        "repos/{owner}/{repo}/issues",
        output=[
            [
                issue(2, "Back from a human", labels=("ready-for-agent", "ready-for-human")),
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
                pull_request(29, "agent/2-back-from-a-human", 2),
                pull_request(30, "agent/3-has-a-pull-request", 3),
                pull_request(31, "agent/6-eligible", 6, head_repository="stranger/project"),
                pull_request(32, "agent/6-eligible", 6, head_repository=None),
            ]
        ],
    )
    for number, blocking in ((2, blockers()), (4, blockers(9)), (6, blockers()), (8, blockers())):
        github.answer(ISSUE.format(number=number) + "/dependencies/blocked_by", output=blocking)
    github.answer(ISSUE.format(number=2), output=issue(2, "Back from a human"))
    github.answer(ISSUE.format(number=6), output=issue(6, "Eligible"))
    dispatcher = bench.steps

    found = asyncio.run(scanned(dispatcher))

    assert found == [{"issue": 6}]
    assert not any(arguments[-1:] == [ISSUE.format(number=8)] for arguments in github.calls)
    assert not any(arguments[:2] == ["issue", "edit"] for arguments in github.calls)


@pytest.mark.parametrize(
    "pulls",
    [[], [pull_request(30, "agent/6-relabeled", 6)]],
    ids=["no-pull-request", "pull-request-open"],
)
def test_the_scan_finds_an_issue_relabeled_ready_for_agent_takes_its_label_off_and_holds_none(
    tmp_path, monkeypatch, pulls
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    relabeled = issue(6, "Relabeled", labels=("ready-for-agent", "ready-for-human"))
    github.answer("repos/{owner}/{repo}/issues", output=[[relabeled, issue(8, "Younger")]])
    github.answer("repos/{owner}/{repo}/pulls", output=[pulls])
    for found in (relabeled, issue(8, "Younger")):
        number = found["number"]
        github.answer(ISSUE.format(number=number) + "/dependencies/blocked_by", output=blockers())
        github.answer(ISSUE.format(number=number), output=found)
    dispatcher = bench.steps

    found = asyncio.run(scanned(dispatcher))

    assert found == [{"issue": 6}, {"issue": 8}]
    assert [arguments for arguments in github.calls if arguments[:2] == ["issue", "edit"]] == [
        ["issue", "edit", "6", "--remove-label", "ready-for-human"]
    ]


def test_the_scan_reads_an_issue_just_labeled_that_the_issue_list_does_not_show_yet(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
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
    dispatcher = bench.steps

    assert asyncio.run(scanned(dispatcher, labeled)) == [{"issue": 5}]


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
def test_a_signal_that_cannot_change_which_issue_is_eligible_finds_nothing_and_asks_nothing(
    tmp_path, monkeypatch, delivery
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    dispatcher = bench.steps

    assert asyncio.run(scanned(dispatcher, delivery)) == []
    assert github.calls == []


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
    bench = coder_at(tmp_path)
    workspace = implemented(bench, BRANCH["branch"])
    (workspace / ".scratch").mkdir()
    writes = "" if verdict is None else f"open('.scratch/review.md', 'w').write({verdict!r})\n"
    stub_client(tmp_path, monkeypatch, "claude", writes + streaming(CLAUDE_STREAM))
    dispatcher = bench.steps

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


PULL = {
    "number": 42,
    "state": "open",
    "head": {"ref": BRANCH["branch"], "sha": "head2"},
    "user": {"login": "coder"},
    "labels": [],
    "requested_reviewers": [],
}


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
                    "author": {
                        "__typename": "Bot" if author.endswith("[bot]") else "User",
                        "login": (login := author.removesuffix("[bot]")),
                    },
                    "authorAssociation": association,
                    "body": f"{login} says.",
                }
                for author, association in comments
            ]
        },
    }


def submitted(author: str, association: str, commit: str = "head2") -> dict[str, object]:
    """A review as GitHub's REST API lists it."""
    kind = "Bot" if author.endswith("[bot]") else "User"
    return {
        "user": {"login": author, "type": kind},
        "author_association": association,
        "commit_id": commit,
    }


def check_run(
    status: str,
    app: str = "github-actions",
    conclusion: str = "success",
    name: str = "lint",
    *,
    run: int = 1,
    started_at: str | None = "2026-10-25T10:00:00Z",
) -> dict:
    """A check run as GitHub's REST API lists it."""
    return {
        "id": run,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started_at,
        "html_url": f"https://github.com/owner/project/runs/{run}",
        "app": {"slug": app},
    }


def answer_pull_request(
    github: FakeGitHub,
    *,
    pull: Mapping[str, object] = PULL,
    checks: Sequence[str] = ("completed",),
    runs: Sequence[dict] = (),
    threads: Sequence[dict[str, object]] = (),
    reviews: Sequence[dict[str, object]] = (),
    comments: Sequence[dict[str, object]] = (),
    labels: Sequence[str] = ("merge-ready",),
) -> None:
    """Answer what assess reads of pull request 42 as the coder: its check runs, one successful
    check for each of *checks* by its status and then *runs*, its review threads, its reviews, its
    comments, and the repository's *labels*."""
    runs = [
        *(check_run(status, name=f"build {index}") for index, status in enumerate(checks)),
        *runs,
    ]
    github.answer("repos/{owner}/{repo}/labels", output=[[{"name": name} for name in labels]])
    github.answer("repos/{owner}/{repo}/pulls/42/reviews", output=[list(reviews)])
    github.answer("repos/{owner}/{repo}/issues/42/comments", output=[list(comments)])
    github.answer("repos/{owner}/{repo}/pulls/42", output=pull)
    github.answer(
        "repos/{owner}/{repo}/commits/head2/check-runs",
        output=[{"total_count": len(runs), "check_runs": runs}],
    )
    github.answer(
        "graphql",
        output=[{"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}],
    )
    github.answer("user", "--jq", output="coder")
    github.answer("repo", "view", output=REPOSITORY)


async def assessed(dispatcher: WorkbenchSteps, *, quiet: bool = False) -> StepResult:
    """What assess answers, after a wait that moved on by itself when *quiet*."""
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=CodeStepRun(call="assess_pull_request"),
            origin=origin("assess"),
            work_item={"issue": 7},
            results=BRANCH | {"pr": 42} | ({"quiet": True} if quiet else {}),
        ),
    )


def labelled(github: FakeGitHub) -> list[list[str]]:
    return [arguments for arguments in github.calls if arguments[:2] == ["pr", "edit"]]


def created_labels(github: FakeGitHub) -> list[list[str]]:
    return [arguments for arguments in github.calls if arguments[:2] == ["label", "create"]]


def test_assess_labels_a_pull_request_a_trusted_reviewer_reviewed_on_its_head_merge_ready(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        threads=[
            review_thread("T1", ("owner", "OWNER"), ("coder", "OWNER")),
            review_thread("T2", ("stranger", "NONE"), resolved=True),
        ],
        reviews=[submitted("greptile-apps[bot]", "NONE")],
    )
    dispatcher = bench.steps

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
    ("labels", "created"), [((), 1), (("merge-ready",), 0)], ids=["missing", "present"]
)
def test_assess_creates_the_merge_ready_label_only_when_the_repository_lacks_it(
    tmp_path, monkeypatch, labels, created
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, reviews=[submitted("owner", "OWNER")], labels=labels)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert (result.ending, result.outcome) == (StepEnding.CLEAN, "merge-ready"), result.summary
        assert len(created_labels(github)) == created
        writes = [arguments[:2] for arguments in github.calls if arguments[0] in {"label", "pr"}]
        assert writes == [["label", "create"]] * created + [["pr", "edit"]]

    asyncio.run(scenario())


@pytest.mark.parametrize("appeared", [True, False], ids=["created-meanwhile", "still-missing"])
def test_assess_whose_label_create_fails_goes_on_only_once_the_label_exists(
    tmp_path, monkeypatch, appeared
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    github.answer("repos/{owner}/{repo}/labels", output=[[]], once=True)
    github.answer("label", "create", error="label with name already exists")
    answer_pull_request(
        github, reviews=[submitted("owner", "OWNER")], labels=("merge-ready",) if appeared else ()
    )
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert len(created_labels(github)) == 1
        if appeared:
            assert (result.ending, result.outcome) == (StepEnding.CLEAN, "merge-ready")
            assert len(labelled(github)) == 1
            return
        assert result.ending is StepEnding.FAILED
        assert "label with name already exists" in str(result.summary)
        assert labelled(github) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("checks", "reviews"),
    [
        (("completed", "in_progress"), [submitted("owner", "OWNER")]),
        (("completed",), [submitted("owner", "OWNER", commit="head1")]),
        (("completed",), [submitted("stranger", "NONE")]),
        (("completed",), [submitted("coder", "OWNER")]),
        (("completed",), [{"user": None, "author_association": "NONE", "commit_id": "head2"}]),
        (("completed",), [submitted("greptile-apps", "NONE")]),
    ],
    ids=[
        "check-running",
        "earlier-head",
        "untrusted-reviewer",
        "own-replies",
        "deleted-reviewer",
        "user-named-as-an-app",
    ],
)
def test_assess_waits_until_a_trusted_reviewer_reviews_the_head_and_no_check_runs(
    tmp_path, monkeypatch, checks, reviews
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, checks=checks, reviews=reviews)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert (result.outcome, result.values) == ("waiting", {})
        assert labelled(github) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("app", "conclusion", "outcome"),
    [
        ("greptile-apps", "success", "merge-ready"),
        ("greptile-apps", "failure", "waiting"),
        ("cursor", "success", "waiting"),
    ],
    ids=["clean", "findings", "untrusted-app"],
)
def test_assess_takes_a_trusted_review_apps_successful_check_run_on_the_head_as_a_review(
    tmp_path, monkeypatch, app, conclusion, outcome
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, runs=[check_run("completed", app, conclusion)])
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == outcome
        assert len(labelled(github)) == (outcome == "merge-ready")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("checks", "outcome"),
    [(("completed",), "merge-ready"), (("completed", "in_progress"), "waiting")],
    ids=["checks-finished", "check-running"],
)
def test_assess_after_a_quiet_wait_stops_waiting_for_a_reviewer_once_no_check_runs(
    tmp_path, monkeypatch, checks, outcome
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, checks=checks)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher, quiet=True)
        commented = [arguments for arguments in github.calls if arguments[:2] == ["pr", "comment"]]

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == outcome
        if outcome == "waiting":
            assert (commented, labelled(github)) == ([], [])
            return
        assert result.values == {"merge_ready": True}
        [(*_, said)] = commented
        assert said.startswith("No reviewer answered on this head")
        assert len(labelled(github)) == 1

    asyncio.run(scenario())


def test_assess_run_twice_comments_creates_the_label_labels_and_asks_for_the_review_once(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, labels=())
    dispatcher = bench.steps

    async def scenario() -> None:
        await assessed(dispatcher, quiet=True)
        [(*_, said)] = [
            arguments for arguments in github.calls if arguments[:2] == ["pr", "comment"]
        ]
        github.forget()
        answer_pull_request(
            github,
            pull=PULL
            | {"labels": [{"name": "merge-ready"}], "requested_reviewers": [{"login": "owner"}]},
            comments=[{"body": said, "user": {"login": "coder", "type": "User"}}],
        )
        result = await assessed(dispatcher, quiet=True)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert (result.outcome, result.values) == ("merge-ready", {"merge_ready": True})
        assert [arguments for arguments in github.calls if arguments[:2] == ["pr", "comment"]] == [
            ["pr", "comment", "42", "--body", said]
        ]
        assert labelled(github) == [
            ["pr", "edit", "42", "--add-label", "merge-ready", "--add-reviewer", "owner"]
        ]
        assert len(created_labels(github)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("conclusion", "reviews", "quiet"),
    [
        ("failure", [submitted("owner", "OWNER")], False),
        ("timed_out", [submitted("owner", "OWNER")], False),
        ("failure", [], True),
    ],
    ids=["failed", "timed-out", "failed-after-a-quiet-wait"],
)
def test_assess_sends_a_check_that_failed_on_the_head_back_for_repair(
    tmp_path, monkeypatch, conclusion, reviews, quiet
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        runs=[check_run("completed", conclusion=conclusion, name="pytest")],
        reviews=reviews,
    )
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher, quiet=quiet)
        failure = (
            "Checks failed on the pull request's head:\n"
            f"- pytest ({conclusion}): https://github.com/owner/project/runs/1"
        )

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == "checks-failed"
        assert result.values == {"failure": failure, "pr": 42}
        assert result.summary == failure
        assert [arguments for arguments in github.calls if arguments[0] == "pr"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("conclusion", "outcome"),
    [
        ("cancelled", "waiting"),
        ("action_required", "waiting"),
        ("startup_failure", "waiting"),
        ("neutral", "merge-ready"),
        ("skipped", "merge-ready"),
        ("stale", "merge-ready"),
    ],
)
def test_assess_waits_on_a_check_no_coding_session_can_repair_and_passes_one_that_did_not_fail(
    tmp_path, monkeypatch, conclusion, outcome
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        runs=[check_run("completed", conclusion=conclusion)],
        reviews=[submitted("owner", "OWNER")],
    )
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher, quiet=True)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == outcome
        assert len(labelled(github)) == (outcome == "merge-ready")

    asyncio.run(scenario())


OLDER = "2026-10-25T10:00:00Z"
NEWER = "2026-10-25T10:00:05Z"
#: A workflow run's checks that a newer workflow run on the same head replaced, newest first.
REPLACED = [
    check_run("completed", run=2, started_at=NEWER),
    check_run("completed", conclusion="cancelled", run=1, started_at=OLDER),
]


@pytest.mark.parametrize(
    ("runs", "outcome"),
    [
        (REPLACED, "merge-ready"),
        (
            [
                check_run("completed", conclusion="failure", run=1, started_at=OLDER),
                check_run("completed", run=2, started_at=NEWER),
            ],
            "merge-ready",
        ),
        (
            [
                check_run("completed", run=2),
                check_run("completed", conclusion="failure", run=1),
            ],
            "merge-ready",
        ),
        (
            [
                check_run("completed", run=1, started_at=OLDER),
                check_run("in_progress", run=2, started_at=NEWER),
            ],
            "waiting",
        ),
        (
            [
                check_run("completed", run=1, started_at=OLDER),
                check_run("queued", run=2, started_at=None),
            ],
            "waiting",
        ),
        (
            [
                check_run("completed", run=1, started_at=OLDER),
                check_run("completed", conclusion="cancelled", run=2, started_at=NEWER),
            ],
            "waiting",
        ),
    ],
    ids=[
        "replaced-run-cancelled",
        "failed-then-passed",
        "same-start-higher-id-passed",
        "newest-running",
        "newest-not-started",
        "newest-cancelled",
    ],
)
def test_assess_judges_only_the_newest_run_of_each_check(tmp_path, monkeypatch, runs, outcome):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, checks=(), runs=runs, reviews=[submitted("owner", "OWNER")])
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == outcome
        assert len(labelled(github)) == (outcome == "merge-ready")

    asyncio.run(scenario())


def test_assess_after_a_quiet_wait_passes_a_check_whose_replaced_run_was_cancelled(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, checks=(), runs=REPLACED)
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher, quiet=True)
        commented = [arguments for arguments in github.calls if arguments[:2] == ["pr", "comment"]]

        assert result.ending is StepEnding.CLEAN, result.summary
        assert (result.outcome, result.values) == ("merge-ready", {"merge_ready": True})
        [(*_, said)] = commented
        assert said.startswith("No reviewer answered on this head")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("runs", "failed"),
    [
        (
            [
                check_run("completed", run=1, started_at=OLDER),
                check_run("completed", conclusion="failure", run=2, started_at=NEWER),
            ],
            2,
        ),
        (
            [
                check_run("completed", conclusion="failure", run=1, started_at=OLDER),
                check_run("completed", "circleci", run=2, started_at=NEWER),
            ],
            1,
        ),
    ],
    ids=["passed-then-failed", "same-name-from-another-app"],
)
def test_assess_sends_back_the_newest_run_of_a_check_that_failed(
    tmp_path, monkeypatch, runs, failed
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, checks=(), runs=runs, reviews=[submitted("owner", "OWNER")])
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == "checks-failed"
        assert result.summary == (
            "Checks failed on the pull request's head:\n"
            f"- lint (failure): https://github.com/owner/project/runs/{failed}"
        )

    asyncio.run(scenario())


def test_assess_answers_review_threads_before_it_repairs_a_failed_check(tmp_path, monkeypatch):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        runs=[check_run("completed", conclusion="failure")],
        threads=[review_thread("T1", ("owner", "OWNER"))],
    )
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == "feedback"
        assert [thread["id"] for thread in json.loads(str(result.values["feedback"]))] == ["T1"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("merged", "outcome", "values"),
    [(True, "merged", {"merge_ready": True}), (False, "closed", {})],
)
def test_assess_ends_the_run_of_a_pull_request_that_was_merged_or_closed(
    tmp_path, monkeypatch, merged, outcome, values
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, pull=PULL | {"state": "closed", "merged": merged})
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert (result.outcome, result.values) == (outcome, values)
        assert labelled(github) == []

    asyncio.run(scenario())


def test_assess_hands_a_fix_round_every_actionable_thread_trusted_authors_wrote(
    tmp_path, monkeypatch
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        threads=[
            review_thread("T1", ("owner", "OWNER"), ("coder", "OWNER"), ("owner", "OWNER")),
            review_thread("T2", ("greptile-apps[bot]", "NONE")),
            review_thread("T3", ("helper", "COLLABORATOR"), ("coder", "OWNER")),
            review_thread("T4", ("owner", "OWNER"), resolved=True),
            review_thread("T5", ("stranger", "NONE")),
        ],
        reviews=[submitted("owner", "OWNER")],
    )
    dispatcher = bench.steps

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
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(
        github,
        threads=[review_thread("T1", ("chatgpt-codex-connector[bot]", "NONE"))],
        reviews=[submitted("chatgpt-codex-connector[bot]", "NONE")],
    )
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.outcome == "feedback"
        assert [thread["id"] for thread in json.loads(str(result.values["feedback"]))] == ["T1"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("thread", "author"),
    [
        (review_thread("T5", ("stranger", "NONE")), "stranger"),
        (
            review_thread(
                "T6", ("owner", "OWNER"), ("stranger", "CONTRIBUTOR"), ("owner", "OWNER")
            ),
            "stranger",
        ),
        (review_thread("T7", ("greptile-apps", "NONE")), "greptile-apps"),
    ],
    ids=["wrote-it", "joined-it", "user-named-as-an-app"],
)
def test_feedback_from_an_untrusted_author_starts_no_fix_round_and_needs_a_human(
    tmp_path, monkeypatch, thread, author
):
    bench = coder_at(tmp_path)
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    answer_pull_request(github, threads=[thread], reviews=[submitted("owner", "OWNER")])
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await assessed(dispatcher)

        assert result.ending is StepEnding.FAILED
        assert result.summary == (
            'Tool "assess_pull_request" failed: UntrustedFeedback: '
            f"Review feedback from untrusted authors needs a human: {author}."
        )
        assert result.values == {}
        assert labelled(github) == []

    asyncio.run(scenario())


def pushed_branch(tmp_path: Path) -> Bench:
    """The workbench with the run worktree of the run's branch, which origin holds, and scratch
    files excluded as the first step leaves them."""
    bench = coder_at(tmp_path)
    worktree = implemented(bench, BRANCH["branch"])
    git(worktree, "push", "-u", "origin", BRANCH["branch"])
    (bench.workspace / ".git" / "info" / "exclude").write_text(".scratch/\n")
    return bench


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


def fixed_by(commit: str) -> dict[str, dict[str, object]]:
    """A reply that says the thread T1 is fixed, naming *commit* as holding the fix."""
    return {"T1": {"fixed": True, "commit": commit, "reply": "Renamed it."}}


FIXED_T1 = fixed_by("HEAD")


async def answered(dispatcher: WorkbenchSteps, results: Mapping[str, StepValue]) -> StepResult:
    return await run_in(
        dispatcher,
        StepRunCommand(
            step=ClientStepRun(
                client=CodingClient.CLAUDE, prompt="Answer them.", timeout_seconds=30
            ),
            hook="check_answers",
            origin=origin("answer"),
            work_item={"issue": 7},
            results=BRANCH | {"pr": 42} | dict(results),
        ),
    )


@pytest.mark.parametrize(
    ("then", "results", "ending", "reason"),
    [
        (FIXES + writes_replies(FIXED_T1), {"feedback": THREADS}, StepEnding.CLEAN, None),
        (
            FIXES + writes_replies(FIXED_T1) + WRITES_WORDS,
            {"feedback": THREADS},
            StepEnding.CLEAN,
            None,
        ),
        (
            writes_replies({"T1": {"fixed": False, "reply": "DARK is the house style."}}),
            {"feedback": THREADS},
            StepEnding.CLEAN,
            None,
        ),
        ("", {}, StepEnding.CLEAN, None),
        (
            FIXES + writes_replies({"T1": {"fixed": True, "reply": "Renamed it."}}),
            {"feedback": THREADS},
            StepEnding.FAILED,
            'UnansweredThreads: The reply to thread T1 is not {"fixed", "reply"}, with a '
            '"commit" when fixed.',
        ),
        (
            FIXES + writes_replies(fixed_by("0123456789abc")),
            {"feedback": THREADS},
            StepEnding.FAILED,
            "UncommittedWork: The reply to thread T1 names 0123456789abc, which is no commit "
            "of agent/7-add-dark-mode ahead of main.",
        ),
        (
            FIXES + writes_replies(fixed_by("stray")),
            {"feedback": THREADS},
            StepEnding.FAILED,
            "UncommittedWork: The reply to thread T1 names stray, which is no commit of "
            "agent/7-add-dark-mode ahead of main.",
        ),
        (
            FIXES + writes_replies(fixed_by("origin/main")),
            {"feedback": THREADS},
            StepEnding.FAILED,
            "UncommittedWork: The reply to thread T1 names origin/main, which is no commit of "
            "agent/7-add-dark-mode ahead of main.",
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
        "fixed-and-rewritten",
        "explained",
        "no-feedback-yet",
        "fix-names-no-commit",
        "no-such-commit",
        "commit-off-the-branch",
        "commit-at-the-base",
        "no-replies",
        "wrong-thread",
    ],
)
def test_the_answer_hook_records_a_reply_for_every_thread_once_its_fixes_are_committed(
    tmp_path, monkeypatch, then, results, ending, reason
):
    bench = pushed_branch(tmp_path)
    workspace = run_worktree(bench)
    # A commit on no branch, after the branch's head.
    git(
        workspace,
        "tag",
        "stray",
        git(workspace, "commit-tree", "-p", "HEAD", "-m", "Stray", "HEAD^{tree}"),
    )
    stub_client(tmp_path, monkeypatch, "claude", then + streaming(CLAUDE_STREAM))
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await answered(dispatcher, results)

        assert result.ending is ending, result.summary
        assert not (workspace / ".scratch" / "review-replies.json").exists()
        if reason is not None:
            assert f'Check "check_answers" failed: {reason}' in result.summary
        elif "feedback" in results:
            replies = json.loads(str(result.values.pop("replies")))
            assert set(replies) == {"T1"}
            if replies["T1"]["fixed"]:
                assert replies["T1"]["commit"] == git(workspace, "rev-parse", "HEAD")
            # An unwritten title or body is recorded as empty, over an earlier round's.
            assert result.values == (WORDS if WRITES_WORDS in then else {"title": "", "body": ""})
            assert not any((workspace / ".scratch").glob("pr-*"))
        else:
            assert result.values == {}

    asyncio.run(scenario())


def test_the_answer_hook_takes_a_fix_an_already_pushed_commit_holds_by_its_full_sha(
    tmp_path, monkeypatch
):
    bench = pushed_branch(tmp_path)
    workspace = run_worktree(bench)
    pushed = git(workspace, "rev-parse", "HEAD")
    stub_client(
        tmp_path,
        monkeypatch,
        "claude",
        writes_replies(fixed_by(pushed[:7])) + streaming(CLAUDE_STREAM),
    )
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await answered(dispatcher, {"feedback": THREADS})

        assert result.ending is StepEnding.CLEAN, result.summary
        assert json.loads(str(result.values["replies"])) == fixed_by(pushed)

    asyncio.run(scenario())


def test_publish_after_a_fix_round_pushes_it_and_replies_on_each_thread_it_answered(
    tmp_path, monkeypatch
):
    bench = pushed_branch(tmp_path)
    workspace = run_worktree(bench)
    pushed = git(workspace, "rev-parse", "HEAD")
    commit(workspace, "theme.py", "DARK_MODE = True\n")
    head = git(workspace, "rev-parse", "HEAD")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    replies = {
        "T1": {"fixed": True, "commit": head, "reply": "Renamed it."},
        "T2": {"fixed": True, "commit": pushed, "reply": "Added it before."},
        "T3": {"fixed": False, "reply": "DARK is the house style."},
    }
    dispatcher = bench.steps

    async def scenario() -> None:
        result = await opened(dispatcher, 7, BRANCH | {"pr": 42, "replies": json.dumps(replies)})

        assert result.ending is StepEnding.CLEAN, result.summary
        assert result.summary == "Pushed agent/7-add-dark-mode and replied on 3 review threads."
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
            (False, "T2", f"{pushed}: Added it before."),
            (True, "T2", None),
            (False, "T3", "DARK is the house style."),
        ]
        assert not any(
            arguments[:2] in (["pr", "create"], ["pr", "edit"]) for arguments in github.calls
        )

    asyncio.run(scenario())


def test_publish_after_a_fix_round_applies_only_the_title_and_body_the_round_recorded(
    tmp_path, monkeypatch
):
    bench = pushed_branch(tmp_path)
    workspace = run_worktree(bench)
    commit(workspace, "theme.py", "DARK_MODE = True\n")
    # Words left in the run worktree, which publish never reads.
    (workspace / ".scratch").mkdir()
    (workspace / ".scratch" / "pr-title.txt").write_text("Add a light mode\n")
    (workspace / ".scratch" / "pr-body.md").write_text("Adds a light mode.\n")
    github = FakeGitHub(tmp_path / "github", monkeypatch)
    dispatcher = bench.steps
    fix_round = BRANCH | {"pr": 42, "replies": json.dumps(FIXED_T1)}
    rewrote = {"title": "Add a dark mode named DARK_MODE", "body": "Closes #7\n\nAdds DARK_MODE."}

    async def scenario() -> None:
        first = await opened(dispatcher, 7, fix_round | rewrote)
        second = await opened(dispatcher, 7, fix_round | {"title": "", "body": ""})

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
