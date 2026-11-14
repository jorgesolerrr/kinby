"""The owner reads the workbenches of its lines, and records their repository commands through the
gate, on its hub route."""

import asyncio
from uuid import UUID

import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    WORKBENCH_GET,
    WORKBENCH_OWNED_COMMAND_RUN,
    WORKBENCH_OWNED_COMMANDS_SET,
    WORKBENCH_OWNED_DIRECTORY_LIST,
    WORKBENCH_OWNED_FILE_READ,
    ApprovalRequested,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Method,
    RepositoryCommandList,
    Workbench,
    WorkbenchCommandRunCommand,
    WorkbenchCommandRunResult,
    WorkbenchCommandsSetCommand,
    WorkbenchDirectoryListCommand,
    WorkbenchDirectoryListResult,
    WorkbenchFileReadCommand,
    WorkbenchFileReadResult,
    WorkbenchGetCommand,
)
from kinby.core.dispatcher import Dispatcher
from tests.test_browser_tools import _parked
from tests.test_factory_runs import intake_client
from tests.test_gate import ScriptedModel
from tests.test_hub import created_instance
from tests.test_hub_home_writes import calling
from tests.test_hub_line_intake import Owned, _line, owned
from tests.test_hub_line_owner import DONE, _approved, _output
from tests.test_routine_grants import turn_events
from tests.test_scheduler import call


def _reads(workbench_id: UUID) -> list[tuple[Method, ContractModel]]:
    return [
        (
            WORKBENCH_OWNED_FILE_READ,
            WorkbenchFileReadCommand(workbench_id=workbench_id, path="README.md"),
        ),
        (
            WORKBENCH_OWNED_DIRECTORY_LIST,
            WorkbenchDirectoryListCommand(workbench_id=workbench_id, path="src"),
        ),
        (
            WORKBENCH_OWNED_COMMAND_RUN,
            WorkbenchCommandRunCommand(workbench_id=workbench_id, command="ls src"),
        ),
    ]


async def _turn(dispatcher: Dispatcher, message: str) -> str:
    created = await call(dispatcher, "thread.create")
    await call(dispatcher, "thread.turn.start", thread_id=created.id, message=message)
    return _output(await turn_events(dispatcher, created.id))


async def _workbench(owned: Owned, workbench_id: UUID) -> Workbench:
    record = await owned.client.call(WORKBENCH_GET, WorkbenchGetCommand(workbench_id=workbench_id))
    assert isinstance(record, Workbench), record
    return record


def test_the_owner_reads_a_file_of_its_lines_workbench_on_that_workbench(tmp_path, monkeypatch):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch, ScriptedModel(replies)) as hub:
            line = await _line(hub)
            arguments: dict[str, object] = {
                "workbench_id": str(line.workbench_id),
                "path": "README.md",
            }
            replies.extend([calling("read", "read_workbench_file", arguments), DONE])
            hub.control.workspace_answers = [WorkbenchFileReadResult(text="# CEC backend\n")]

            output = await _turn(hub.dispatcher, "What is this repository?")

            [(endpoint, read)] = hub.control.workspace_reads
            assert endpoint.address == f"http://kinby-{line.workbench_id}:8787"
            assert read == WorkbenchFileReadCommand(
                workbench_id=line.workbench_id, path="README.md"
            )
            assert output == "# CEC backend\n"

    asyncio.run(scenario())


def test_the_owner_lists_a_directory_and_runs_a_shell_read_on_its_lines_workbench(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            hub.control.workspace_answers = [
                WorkbenchDirectoryListResult(entries=["app.py", "tests/"]),
                WorkbenchCommandRunResult(output="app.py\ntests\n"),
            ]
            client = intake_client(hub.hub, hub.home)
            _, listing, running = _reads(line.workbench_id)

            listed = await client.call(*listing)
            ran = await client.call(*running)

            assert listed == WorkbenchDirectoryListResult(entries=["app.py", "tests/"])
            assert ran == WorkbenchCommandRunResult(output="app.py\ntests\n")
            assert [read for _, read in hub.control.workspace_reads] == [listing[1], running[1]]

    asyncio.run(scenario())


@pytest.mark.parametrize("read", [0, 1, 2], ids=["file", "directory", "command"])
def test_the_hub_refuses_each_read_on_a_workbench_the_instance_owns_no_line_on(
    tmp_path, monkeypatch, read
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            other = (await created_instance(hub.client)).instance_id

            refused = await intake_client(hub.hub, other).call(*_reads(line.workbench_id)[read])

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.PERMISSION_DENIED
            assert hub.control.workspace_reads == []

    asyncio.run(scenario())


def test_the_owner_records_a_workbenchs_repository_commands_once_the_user_approves(
    tmp_path, monkeypatch
):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch, ScriptedModel(replies)) as hub:
            line = await _line(hub)
            arguments: dict[str, object] = {
                "workbench_id": str(line.workbench_id),
                "name": "setup",
                "commands": ["uv sync --frozen"],
            }
            replies.extend([calling("record", "record_workbench_commands", arguments), DONE])

            parked = await _parked(hub.dispatcher, "Record the setup list.", ApprovalRequested)
            before = await _workbench(hub, line.workbench_id)
            events = await _approved(hub.dispatcher, parked)
            after = await _workbench(hub, line.workbench_id)

            assert isinstance(parked.payload, ApprovalRequested)
            assert parked.payload.name == "record_workbench_commands"
            assert before.repository_commands == []
            assert after.repository_commands == [
                RepositoryCommandList(name="setup", commands=["uv sync --frozen"])
            ]
            assert "setup" in _output(events)

    asyncio.run(scenario())


def test_the_hub_refuses_repository_commands_from_an_instance_that_owns_no_line_on_the_workbench(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            other = (await created_instance(hub.client)).instance_id

            refused = await intake_client(hub.hub, other).call(
                WORKBENCH_OWNED_COMMANDS_SET,
                WorkbenchCommandsSetCommand(
                    workbench_id=line.workbench_id, name="setup", commands=["rm -rf ."]
                ),
            )

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.PERMISSION_DENIED
            assert (await _workbench(hub, line.workbench_id)).repository_commands == []

    asyncio.run(scenario())
