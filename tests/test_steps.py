"""Factory steps in the instance: step.run through the instance's dispatcher and control socket."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage

from kinby.contracts import (
    AgentStepRun,
    ApprovalRequested,
    ClientStepRun,
    CodeStepRun,
    CodingClient,
    CommandStepRun,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    FactoryRunOrigin,
    InstanceDrainCommand,
    PlanLimit,
    RoutineListResult,
    RoutineRunOutcome,
    StatsGetResult,
    StepEnding,
    StepResult,
    StepRunCommand,
    ThreadListResult,
    ToolResult,
    UsageSource,
)
from kinby.core.dispatcher import Dispatcher, ScheduledDispatcher
from kinby.core.events import EventLog
from kinby.core.runtime import InstanceRuntime
from kinby.hub import ControlEndpoint, HttpInstanceControl
from kinby.instance.layout import PERMISSIONS_NAME
from tests.test_contract_server import TOKEN, served_dispatcher
from tests.test_drain import WaitingRunner, booted, call, opened_thread
from tests.test_gate import ScriptedModel
from tests.test_routines import RoutineModel, fire, instance_at, routine_file, signal_runtime

#: The step each test runs, as the hub names it.
ORIGIN = FactoryRunOrigin(
    factory="checks", run_id=UUID("6f1c2a52-8d1e-4f3b-9a43-2b6f0e3d9c11"), step="test"
)
REVIEW = ORIGIN.model_copy(update={"step": "review"})


def step_instance(
    tmp_path: Path, runner: WaitingRunner | None = None
) -> tuple[InstanceRuntime, Path]:
    """A booted instance over a scripted runner, and its workspace."""
    instance = instance_at(tmp_path)
    workspace = instance.manifest.workspace.path
    workspace.mkdir(parents=True, exist_ok=True)
    return booted(instance, runner or WaitingRunner()), workspace


async def run_step(dispatcher: Dispatcher, *commands: str) -> ContractModel:
    command = StepRunCommand(
        step=CommandStepRun(run=list(commands)), origin=ORIGIN, work_item={"issue": 7}
    )
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


def test_a_command_step_runs_each_command_in_the_workspace_without_a_shell(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        result = await run_step(runtime.dispatcher, "touch made", "echo $HOME > redirected")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.CLEAN
        assert (workspace / "made").is_file()
        assert not (workspace / "redirected").exists()

    asyncio.run(scenario())


def test_a_command_step_fails_at_the_first_command_that_exits_with_another_code(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        result = await run_step(
            runtime.dispatcher, "touch made", "sh -c 'echo broken >&2; exit 3'", "touch never"
        )

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary == "\"sh -c 'echo broken >&2; exit 3'\" exited with code 3.\nbroken"
        assert (workspace / "made").is_file()
        assert not (workspace / "never").exists()

    asyncio.run(scenario())


def test_a_command_still_running_at_the_steps_timeout_is_killed_and_the_step_times_out(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        command = StepRunCommand(
            step=CommandStepRun(run=["sleep 30", "touch never"], timeout_seconds=1),
            origin=ORIGIN,
            work_item={},
        )
        async with asyncio.timeout(10):
            result = await call(runtime.dispatcher, "step.run", **command.model_dump(mode="json"))

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.TIMED_OUT
        assert result.summary == "The commands ran past the step's timeout of 1s."
        assert not (workspace / "never").exists()

    asyncio.run(scenario())


def test_a_command_that_cannot_start_fails_the_step(tmp_path):
    runtime, _ = step_instance(tmp_path)

    async def scenario() -> None:
        result = await run_step(runtime.dispatcher, "kinby-no-such-command --help")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary.startswith('"kinby-no-such-command --help" could not start: ')

    asyncio.run(scenario())


def test_a_step_waits_for_a_running_turn_and_holds_the_instance_like_one(tmp_path):
    runner = WaitingRunner()
    runtime, workspace = step_instance(tmp_path, runner)

    async def scenario() -> None:
        thread = await opened_thread(runtime.dispatcher)
        await call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await asyncio.wait_for(runner.started.wait(), timeout=5)
        stepping = asyncio.create_task(
            run_step(runtime.dispatcher, "touch started", "sleep 0.3", "touch finished")
        )
        await asyncio.sleep(0.2)
        waited = not (workspace / "started").exists()
        runner.release.set()
        async with asyncio.timeout(5):
            while not (workspace / "started").exists():
                await asyncio.sleep(0.01)
        second = await opened_thread(runtime.dispatcher)
        refused = await call(
            runtime.dispatcher, "thread.turn.start", thread_id=str(second), message="Hi"
        )
        draining = asyncio.create_task(runtime.drain(InstanceDrainCommand()))
        await asyncio.sleep(0)
        drain_waited = not draining.done()
        result = await asyncio.wait_for(stepping, timeout=5)
        await asyncio.wait_for(draining, timeout=5)

        assert waited is True
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert drain_waited is True
        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.CLEAN
        assert (workspace / "finished").is_file()

    asyncio.run(scenario())


def test_a_draining_instance_takes_no_step(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        await runtime.drain(InstanceDrainCommand())
        refused = await run_step(runtime.dispatcher, "touch made")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_DRAINING
        assert not (workspace / "made").exists()

    asyncio.run(scenario())


def test_the_hub_runs_a_step_over_the_instances_control_socket(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        async with served_dispatcher(runtime.dispatcher) as address:
            result = await HttpInstanceControl().run_step(
                ControlEndpoint(f"http://{address.host}:{address.port}", TOKEN),
                StepRunCommand(
                    step=CommandStepRun(run=["touch made"]), origin=ORIGIN, work_item={}
                ),
            )

        assert result.ending is StepEnding.CLEAN
        assert (workspace / "made").is_file()

    asyncio.run(scenario())


VERDICT_HOOK = """\
from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def read_verdict(end: StepEnd) -> HookResult:
    verdict = (end.workspace / "verdict").read_text().strip()
    return HookResult(
        values={"verdict": verdict, "ending": end.ending.value, "issue": end.work_item["issue"]},
        outcome=verdict,
    )


@hook
async def record_nothing(end: StepEnd) -> None:
    return None


@hook
def broken(end: StepEnd) -> HookResult:
    raise RuntimeError("the repository is gone")
"""


def hooked_instance(tmp_path: Path) -> tuple[InstanceRuntime, Path]:
    runtime, workspace = step_instance(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "review.py").write_text(VERDICT_HOOK)
    return runtime, workspace


async def run_hooked_step(dispatcher: Dispatcher, hook: str, *commands: str) -> ContractModel:
    command = StepRunCommand(
        step=CommandStepRun(run=list(commands)), hook=hook, origin=ORIGIN, work_item={"issue": 7}
    )
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("command", "ending"),
    [("touch made", StepEnding.CLEAN), ("sh -c 'exit 1'", StepEnding.FAILED)],
)
def test_the_hook_records_the_result_however_the_step_ended(tmp_path, command, ending):
    runtime, workspace = hooked_instance(tmp_path)
    (workspace / "verdict").write_text("changes\n")

    async def scenario() -> None:
        result = await run_hooked_step(runtime.dispatcher, "read_verdict", command)

        assert isinstance(result, StepResult)
        assert result.ending is ending
        assert result.outcome == "changes"
        assert result.values == {"verdict": "changes", "ending": ending.value, "issue": 7}

    asyncio.run(scenario())


def test_a_hook_that_records_nothing_leaves_the_steps_own_result(tmp_path):
    runtime, _ = hooked_instance(tmp_path)

    async def scenario() -> None:
        result = await run_hooked_step(runtime.dispatcher, "record_nothing", "touch made")

        assert result == StepResult(
            ending=StepEnding.CLEAN, summary="Every command exited with code 0."
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("hook", "summary"),
    [
        ("broken", 'Hook "broken" failed: RuntimeError: the repository is gone'),
        ("read_review", 'Hook "read_review" is not one of this instance\'s hooks.'),
    ],
)
def test_a_hook_that_fails_or_is_missing_fails_the_step(tmp_path, hook, summary):
    runtime, _ = hooked_instance(tmp_path)

    async def scenario() -> None:
        result = await run_hooked_step(runtime.dispatcher, hook, "touch made")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary == f"Every command exited with code 0.\n{summary}"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("hook", "summary"),
    [
        ("broken", 'Hook "broken" failed: RuntimeError: the repository is gone'),
        ("read_review", 'Hook "read_review" is not one of this instance\'s hooks.'),
    ],
)
def test_a_timed_out_step_whose_hook_fails_still_times_out(tmp_path, hook, summary):
    runtime, _ = hooked_instance(tmp_path)
    command = StepRunCommand(
        step=CommandStepRun(run=["sleep 30"], timeout_seconds=1),
        hook=hook,
        origin=ORIGIN,
        work_item={},
    )

    async def scenario() -> None:
        async with asyncio.timeout(10):
            result = await call(runtime.dispatcher, "step.run", **command.model_dump(mode="json"))

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.TIMED_OUT
        assert result.summary == f"The commands ran past the step's timeout of 1s.\n{summary}"

    asyncio.run(scenario())


def test_kinbys_default_hooks_record_the_branch_and_find_its_pull_request(tmp_path, monkeypatch):
    runtime, workspace = step_instance(tmp_path)
    git = ("git", "-C", str(workspace))
    subprocess.run((*git, "init", "--quiet", "--initial-branch", "agent/7"), check=True)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    gh = bin_directory / "gh"
    gh.write_text('#!/bin/sh\necho "$@" > "$0.args"\necho \'{"number": 42}\'\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_directory}{os.pathsep}{os.environ['PATH']}")

    async def scenario() -> None:
        branch = await run_hooked_step(runtime.dispatcher, "record_branch", "true")
        command = StepRunCommand(
            step=CommandStepRun(run=["true"]),
            hook="find_pull_request",
            origin=ORIGIN,
            work_item={"issue": 7},
            results={"branch": "agent/7"},
        )
        pull_request = await call(runtime.dispatcher, "step.run", **command.model_dump(mode="json"))

        assert isinstance(branch, StepResult)
        assert branch.values == {"branch": "agent/7"}
        assert isinstance(pull_request, StepResult)
        assert pull_request.values == {"pr": 42}
        assert (bin_directory / "gh.args").read_text() == "pr view agent/7 --json number\n"

    asyncio.run(scenario())


def test_an_instance_hook_is_never_offered_to_the_model(tmp_path):
    instance = instance_at(tmp_path)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "review.py").write_text(VERDICT_HOOK)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "github.py").write_text(GITHUB_TOOL)
    routine_file(instance, "description: News")
    model = RoutineModel()

    asyncio.run(fire(instance, model))

    assert "open_pull_request" in model.tools
    assert not {"read_verdict", "record_nothing", "broken", "record_branch"} & set(model.tools)


GITHUB_TOOL = '''\
from kinby.plugins.tools import tool


@tool(write=True)
def open_pull_request(branch: str, issue: int) -> dict[str, int]:
    """Open the pull request of the branch."""
    if branch == "broken":
        raise RuntimeError("GitHub refused the pull request")
    return {"pr": issue * 10}
'''


def code_runtime(tmp_path: Path) -> tuple[ScheduledDispatcher, RoutineModel]:
    """An instance with a model, so a test can see the model is never asked."""
    instance = instance_at(tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "github.py").write_text(GITHUB_TOOL)
    model = RoutineModel()
    dispatcher, _ = signal_runtime(instance, model)
    return dispatcher, model


async def run_code_step(
    dispatcher: Dispatcher, tool: str, results: dict[str, str]
) -> ContractModel:
    command = StepRunCommand(
        step=CodeStepRun(call=tool),
        origin=ORIGIN,
        work_item={"issue": 7, "repo": "kinby"},
        results=results,
    )
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


def test_a_code_step_calls_its_tool_with_the_runs_values_and_no_model(tmp_path):
    dispatcher, model = code_runtime(tmp_path)

    async def scenario() -> None:
        result = await run_code_step(dispatcher, "open_pull_request", {"branch": "agent/7"})
        threads = await call(dispatcher, "thread.list")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.CLEAN
        assert result.values == {"pr": 70}
        assert model.messages == []
        assert model.tools == []
        assert isinstance(threads, ThreadListResult)
        assert threads.threads == []

    asyncio.run(scenario())


REVIEW_TOOL = '''\
from kinby.plugins.hooks import HookResult
from kinby.plugins.tools import tool


@tool(write=True)
def read_review(issue: int) -> HookResult:
    """Read the review of the issue's pull request."""
    return HookResult(values={"reviewed": True}, outcome="changes")
'''


def test_a_code_steps_tool_names_an_outcome_by_returning_a_hook_result(tmp_path):
    dispatcher, _ = code_runtime(tmp_path)
    (tmp_path / "tools" / "review.py").write_text(REVIEW_TOOL)

    async def scenario() -> None:
        result = await run_code_step(dispatcher, "read_review", {})

        assert result == StepResult(
            ending=StepEnding.CLEAN, outcome="changes", values={"reviewed": True}
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("tool", "summary"),
    [
        ("open_pull_request", 'Tool "open_pull_request" failed: RuntimeError: GitHub refused'),
        ("merge", 'Tool "merge" is not one of this instance\'s tools.'),
    ],
)
def test_a_code_step_whose_tool_fails_or_is_missing_fails(tmp_path, tool, summary):
    dispatcher, _ = code_runtime(tmp_path)

    async def scenario() -> None:
        result = await run_code_step(dispatcher, tool, {"branch": "broken"})

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary.startswith(summary)

    asyncio.run(scenario())


def test_a_factory_runs_failed_steps_never_count_toward_its_intake_routines_failure_streak(
    tmp_path,
):
    dispatcher, model = code_runtime(tmp_path)
    instance = instance_at(tmp_path)
    routine_file(instance, "description: Intake\nmode: full-access")

    async def scenario() -> None:
        await fire(instance, model)
        failed = [
            await run_code_step(dispatcher, "open_pull_request", {"branch": "broken"}),
            await run_step(dispatcher, "sh -c 'exit 1'"),
        ]
        listed = await call(dispatcher, "routine.list")

        assert {result.ending for result in failed if isinstance(result, StepResult)} == {
            StepEnding.FAILED
        }
        assert isinstance(listed, RoutineListResult)
        [intake] = listed.routines
        assert (intake.failure_count, intake.enabled) == (0, True)
        assert intake.last_run is not None
        assert intake.last_run.outcome is RoutineRunOutcome.WORK

    asyncio.run(scenario())


class StepModel(ScriptedModel):
    """A scripted model that keeps every message it was sent."""

    def __init__(self, responses: Sequence[AIMessageChunk]) -> None:
        super().__init__(responses)
        self.messages: list[list[BaseMessage]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(list(messages))
        async for chunk in super().astream(messages):
            yield chunk


class BrokenModel(StepModel):
    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(list(messages))
        raise RuntimeError("the provider is down")
        yield AIMessageChunk(content="")


def agent_runtime(tmp_path: Path, model: StepModel) -> tuple[ScheduledDispatcher, EventLog, Path]:
    """An instance whose turns ask *model*, with the review hooks and the GitHub tool."""
    instance = instance_at(tmp_path)
    workspace = instance.manifest.workspace.path
    workspace.mkdir(parents=True, exist_ok=True)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "review.py").write_text(VERDICT_HOOK)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "github.py").write_text(GITHUB_TOOL)
    dispatcher, log = signal_runtime(instance, model)
    return dispatcher, log, workspace


async def run_agent_step(dispatcher: Dispatcher) -> ContractModel:
    command = StepRunCommand(
        step=AgentStepRun(prompt="Review the branch."),
        hook="read_verdict",
        origin=REVIEW,
        work_item={"issue": 7},
        results={"branch": "agent/7"},
    )
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


def test_an_agent_step_runs_a_turn_with_the_prompt_and_the_runs_values_and_the_hooks_result(
    tmp_path,
):
    model = StepModel([AIMessageChunk(content="The branch does what the issue asks.")])
    dispatcher, _, workspace = agent_runtime(tmp_path, model)
    (workspace / "verdict").write_text("clean\n")

    async def scenario() -> None:
        result = await run_agent_step(dispatcher)

        assert result == StepResult(
            ending=StepEnding.CLEAN,
            outcome="clean",
            values={"verdict": "clean", "ending": "clean", "issue": 7},
            summary="The branch does what the issue asks.",
        )
        [messages] = model.messages
        wake = str(messages[-1].content)
        assert wake.startswith("[Factory checks, step review]\n")
        assert "Review the branch." in wake
        assert '{"work_item": {"issue": 7}, "results": {"branch": "agent/7"}}' in wake

    asyncio.run(scenario())


def test_an_agent_steps_thread_names_its_factory_run_and_step(tmp_path):
    model = StepModel([AIMessageChunk(content="Reviewed.")])
    dispatcher, _, workspace = agent_runtime(tmp_path, model)
    (workspace / "verdict").write_text("clean\n")

    async def scenario() -> None:
        await run_agent_step(dispatcher)
        threads = await call(dispatcher, "thread.list", filter="all")

        assert isinstance(threads, ThreadListResult)
        [thread] = threads.threads
        assert thread.origin == REVIEW
        assert thread.title == "checks · review · issue 7"

    asyncio.run(scenario())


def test_a_gated_tool_in_an_agent_steps_turn_waits_on_the_users_approval(tmp_path):
    model = StepModel(
        [
            AIMessageChunk(
                content="",
                tool_calls=[
                    {
                        "id": "open-1",
                        "name": "open_pull_request",
                        "args": {"branch": "agent/7", "issue": 7},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessageChunk(content="Opened pull request 70."),
        ]
    )
    dispatcher, log, workspace = agent_runtime(tmp_path, model)
    (workspace / "verdict").write_text("clean\n")

    async def scenario() -> None:
        stepping = asyncio.create_task(run_agent_step(dispatcher))
        async with asyncio.timeout(5):
            while not (
                requested := [
                    event
                    for event in log.all_events()
                    if isinstance(event.payload, ApprovalRequested)
                ]
            ):
                await asyncio.sleep(0.01)
        [approval] = requested
        assert isinstance(approval.payload, ApprovalRequested)
        await asyncio.sleep(0.1)
        waited = not stepping.done()
        await call(
            dispatcher,
            "thread.approval.respond",
            thread_id=str(approval.thread_id),
            approval_id=str(approval.payload.approval_id),
            decision="approve",
        )
        result = await asyncio.wait_for(stepping, timeout=5)

        assert waited is True
        assert isinstance(result, StepResult)
        assert (result.ending, result.summary) == (StepEnding.CLEAN, "Opened pull request 70.")
        [tool_result] = [
            event.payload
            for event in log.stored(approval.thread_id)
            if isinstance(event.payload, ToolResult)
        ]
        assert (tool_result.output, tool_result.error) == ("{'pr': 70}", False)

    asyncio.run(scenario())


def test_an_agent_step_whose_turn_fails_still_runs_its_hook(tmp_path):
    dispatcher, _, workspace = agent_runtime(tmp_path, BrokenModel([]))
    (workspace / "verdict").write_text("changes\n")

    async def scenario() -> None:
        result = await run_agent_step(dispatcher)

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.values == {"verdict": "changes", "ending": "failed", "issue": 7}
        assert result.summary == "The turn failed: The model turn failed unexpectedly."

    asyncio.run(scenario())


#: What Claude Code streams for a run that implemented the issue in session claude-1.
CLAUDE_STREAM = [
    {"type": "system", "subtype": "init", "session_id": "claude-1"},
    {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {}}},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "claude-1",
        "result": "Implemented issue 7.",
        "num_turns": 3,
        "modelUsage": {
            "claude-opus-5-5": {
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadInputTokens": 50,
                "cacheCreationInputTokens": 10,
            }
        },
    },
]
#: What Codex streams for the same run, in thread codex-1.
CODEX_STREAM = [
    {"type": "thread.started", "thread_id": "codex-1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Done."}},
    {
        "type": "item.completed",
        "item": {"id": "i2", "type": "agent_message", "text": "Implemented issue 7."},
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 160, "cached_input_tokens": 50, "output_tokens": 20},
    },
]


#: The API keys a client would bill over its subscription login, all set for every stub client.
API_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY")


def stub_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, then: str) -> Path:
    """Put an executable *name* first on PATH that records how it was called, then runs *then*.

    Returns the file each call is recorded in, one JSON object per line.
    """
    calls = tmp_path / f"{name}.calls"
    executable = tmp_path / "bin" / name
    executable.parent.mkdir(exist_ok=True)
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        f"with open({str(calls)!r}, 'a') as calls:\n"
        "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': sys.stdin.read(),\n"
        f"               'api_keys': [name for name in {API_KEYS!r} if name in os.environ]}},\n"
        "              calls)\n"
        "    calls.write('\\n')\n"
        f"{then}\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ['PATH']}")
    for variable in API_KEYS:
        monkeypatch.setenv(variable, "sk-test")
    return calls


def streaming(events: Sequence[Mapping[str, object]]) -> str:
    """The stub's body that streams *events* as JSON lines and exits with code zero."""
    return "".join(f"print({json.dumps(json.dumps(event))})\n" for event in events)


@dataclass(frozen=True)
class ClientCall:
    """How the stub client was called."""

    argv: list[str]
    cwd: str
    stdin: str
    #: The API keys the client saw.
    api_keys: list[str]


def recorded_calls(calls: Path) -> list[ClientCall]:
    return [ClientCall(**json.loads(line)) for line in calls.read_text().splitlines()]


async def run_client_step(
    dispatcher: Dispatcher, client: str, *, resume: str | None = None, hook: str | None = None
) -> ContractModel:
    command = StepRunCommand(
        step=ClientStepRun(
            client=CodingClient(client),
            prompt="Implement the issue.",
            resume=resume,
            timeout_seconds=1,
        ),
        hook=hook,
        origin=ORIGIN,
        work_item={"issue": 7},
        results={"branch": "agent/7"},
    )
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("client", "stream", "session", "arguments", "kept"),
    [
        (
            "claude",
            CLAUDE_STREAM,
            "claude-1",
            ["-p", "--permission-mode", "acceptEdits"],
            ["OPENAI_API_KEY", "CODEX_API_KEY"],
        ),
        (
            "codex",
            CODEX_STREAM,
            "codex-1",
            ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox"],
            ["ANTHROPIC_API_KEY"],
        ),
    ],
)
def test_a_client_step_runs_the_client_in_the_workspace_with_the_prompt_and_keeps_its_session(
    tmp_path, monkeypatch, client, stream, session, arguments, kept
):
    runtime, workspace = step_instance(tmp_path)
    calls = stub_client(tmp_path, monkeypatch, client, streaming(stream))

    async def scenario() -> None:
        result = await run_client_step(runtime.dispatcher, client)

        assert result == StepResult(
            ending=StepEnding.CLEAN, summary="Implemented issue 7.", session=session
        )
        [called] = recorded_calls(calls)
        assert called.argv[: len(arguments)] == arguments
        assert called.cwd == str(workspace)
        assert called.stdin.startswith("Implement the issue.\n")
        assert '{"work_item": {"issue": 7}, "results": {"branch": "agent/7"}}' in called.stdin
        assert called.api_keys == kept

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("then", "ending", "session"),
    [
        (streaming(CLAUDE_STREAM), StepEnding.CLEAN, "claude-1"),
        ('print(\'{"type": "result", "subtype": "succ\')', StepEnding.CLEAN, None),
        (streaming(CLAUDE_STREAM[:2]) + "time.sleep(10)", StepEnding.TIMED_OUT, None),
        (
            streaming(CLAUDE_STREAM[:2]) + "sys.stdout.flush()\nos.kill(os.getpid(), 9)",
            StepEnding.FAILED,
            None,
        ),
    ],
    ids=["clean", "malformed", "timeout", "kill"],
)
def test_a_client_steps_hook_records_its_result_however_the_client_ended(
    tmp_path, monkeypatch, then, ending, session
):
    runtime, workspace = hooked_instance(tmp_path)
    (workspace / "verdict").write_text("changes\n")
    stub_client(tmp_path, monkeypatch, "claude", then)

    async def scenario() -> None:
        result = await run_client_step(runtime.dispatcher, "claude", hook="read_verdict")

        assert isinstance(result, StepResult)
        assert result.ending is ending
        assert result.outcome == "changes"
        assert result.values == {"verdict": "changes", "ending": ending.value, "issue": 7}
        assert result.session == session

    asyncio.run(scenario())


def test_a_client_that_runs_past_its_timeout_is_killed_and_ends_the_attempt_as_a_timeout(
    tmp_path, monkeypatch
):
    runtime, workspace = step_instance(tmp_path)
    stub_client(tmp_path, monkeypatch, "codex", "time.sleep(1.5)\nopen('late', 'w').close()")

    async def scenario() -> None:
        result = await run_client_step(runtime.dispatcher, "codex")
        await asyncio.sleep(1)

        assert result == StepResult(
            ending=StepEnding.TIMED_OUT,
            summary="codex ran past its 1-second timeout and was killed.",
        )
        assert not (workspace / "late").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("client", "stream", "session", "arguments"),
    [
        ("claude", CLAUDE_STREAM, "claude-1", ["--resume", "claude-1"]),
        ("codex", CODEX_STREAM, "codex-1", ["exec", "resume", "codex-1", "-"]),
    ],
)
def test_a_client_step_that_resumes_continues_the_earlier_steps_session(
    tmp_path, monkeypatch, client, stream, session, arguments
):
    runtime, _ = step_instance(tmp_path)
    calls = stub_client(tmp_path, monkeypatch, client, streaming(stream))

    async def scenario() -> None:
        result = await run_client_step(runtime.dispatcher, client, resume=session)

        assert isinstance(result, StepResult)
        assert (result.ending, result.session) == (StepEnding.CLEAN, session)
        [called] = recorded_calls(calls)
        assert [argument for argument in called.argv if argument in arguments] == arguments

    asyncio.run(scenario())


def subscription_use(stats: ContractModel, source: UsageSource) -> tuple[int, int, int, int, int]:
    """The runs and tokens stats.get counts for *source*: runs, input, output, read, created."""
    assert isinstance(stats, StatsGetResult)
    [use] = [use for use in stats.total.subscriptions if use.usage_source is source]
    return (
        use.runs,
        use.input_tokens,
        use.output_tokens,
        use.cache_read_tokens,
        use.cache_creation_tokens,
    )


def test_each_client_run_is_a_delegated_run_of_the_instance(tmp_path, monkeypatch):
    runtime, _ = step_instance(tmp_path)
    stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))

    async def scenario() -> None:
        await run_client_step(runtime.dispatcher, "claude")
        stats = await call(runtime.dispatcher, "stats.get")

        assert subscription_use(stats, UsageSource.CLAUDE_SUBSCRIPTION) == (1, 160, 20, 50, 10)
        assert isinstance(stats, StatsGetResult)
        assert [use.runs for use in stats.plan_use] == [1, 1, 0, 0]
        assert stats.limits == []

    asyncio.run(scenario())


def test_a_resumed_codex_thread_reports_its_own_tokens_not_the_threads_running_total(
    tmp_path, monkeypatch
):
    runtime, _ = step_instance(tmp_path)
    resumed = [
        *CODEX_STREAM[:-1],
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 260, "cached_input_tokens": 80, "output_tokens": 50},
        },
    ]

    async def scenario() -> None:
        stub_client(tmp_path, monkeypatch, "codex", streaming(CODEX_STREAM))
        await run_client_step(runtime.dispatcher, "codex")
        stub_client(tmp_path, monkeypatch, "codex", streaming(resumed))
        await run_client_step(runtime.dispatcher, "codex", resume="codex-1")
        stats = await call(runtime.dispatcher, "stats.get")

        assert subscription_use(stats, UsageSource.CHATGPT_SUBSCRIPTION) == (2, 260, 50, 80, 0)

    asyncio.run(scenario())


def test_a_client_run_the_plan_refused_names_when_its_window_resets(tmp_path, monkeypatch):
    runtime, _ = step_instance(tmp_path)
    resets_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    refused = [
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": int(resets_at.timestamp())},
        },
        {"type": "result", "subtype": "error", "is_error": True, "session_id": "claude-2"},
    ]
    stub_client(tmp_path, monkeypatch, "claude", streaming(refused) + "sys.exit(1)")

    async def scenario() -> None:
        result = await run_client_step(runtime.dispatcher, "claude")
        stats = await call(runtime.dispatcher, "stats.get")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert isinstance(stats, StatsGetResult)
        assert stats.limits == [
            PlanLimit(usage_source=UsageSource.CLAUDE_SUBSCRIPTION, resets_at=resets_at)
        ]

    asyncio.run(scenario())


def test_an_agent_step_whose_turn_cannot_start_on_a_broken_instance_still_runs_its_hook(
    tmp_path,
):
    dispatcher, _, workspace = agent_runtime(tmp_path, StepModel([]))
    (workspace / "verdict").write_text("changes\n")
    (tmp_path / PERMISSIONS_NAME).write_text("mode = [\n")

    async def scenario() -> None:
        result = await run_agent_step(dispatcher)

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.values == {"verdict": "changes", "ending": "failed", "issue": 7}
        assert result.summary.startswith(f"The turn could not start: {PERMISSIONS_NAME}: ")

    asyncio.run(scenario())
