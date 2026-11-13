"""A workbench keeps its clone, its repository commands and its picks, through the hub contract."""

import asyncio
from pathlib import Path
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    WORKBENCH_COMMANDS_SET,
    WORKBENCH_PICKS_GET,
    WORKBENCH_PICKS_SET,
    WORKBENCH_RECREATE,
    ErrorCode,
    ErrorEnvelope,
    LibrarySource,
    OperationState,
    PickState,
    RepositoryCommandList,
    SkillPick,
    StorageItem,
    StorageKind,
    Workbench,
    WorkbenchCommandsSetCommand,
    WorkbenchCreateCommand,
    WorkbenchPicksGetCommand,
    WorkbenchPicksResult,
    WorkbenchPicksSetCommand,
    WorkbenchRecreateCommand,
)
from kinby.library import SHIPPED_SKILLS
from tests.test_hub import HUB_SCOPES, FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_workbenches import created, finished, got, started

LANDING = WorkbenchCreateCommand(name="landing")
TDD = SkillPick(name="tdd", source=LibrarySource.SHIPPED, state=PickState.UP_TO_DATE)
UNSLOP = SkillPick(name="unslop", source=LibrarySource.SHIPPED, state=PickState.UP_TO_DATE)


async def _commands_set(
    client: ContractClient, workbench_id: UUID, name: str, commands: list[str]
) -> Workbench:
    answer = await client.call(
        WORKBENCH_COMMANDS_SET,
        WorkbenchCommandsSetCommand(workbench_id=workbench_id, name=name, commands=commands),
    )
    assert isinstance(answer, Workbench), answer
    return answer


async def _picks(client: ContractClient, workbench_id: UUID) -> WorkbenchPicksResult:
    answer = await client.call(
        WORKBENCH_PICKS_GET, WorkbenchPicksGetCommand(workbench_id=workbench_id)
    )
    assert isinstance(answer, WorkbenchPicksResult), answer
    return answer


async def _pick(client: ContractClient, workbench_id: UUID, *skills: str) -> WorkbenchPicksResult:
    """Set the picks as a client does: with the hash of the lock it just read."""
    read = (await _picks(client, workbench_id)).hash
    answer = await client.call(
        WORKBENCH_PICKS_SET,
        WorkbenchPicksSetCommand(workbench_id=workbench_id, skills=list(skills), hash=read),
    )
    assert isinstance(answer, WorkbenchPicksResult), answer
    return answer


def _files(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }


def test_a_workbench_container_clones_its_source_and_a_recreation_keeps_its_workspace(tmp_path):
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", runtime=runtime, control=FakeControl())
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await started(client, runtime)

        recreate = await client.call(
            WORKBENCH_RECREATE, WorkbenchRecreateCommand(workbench_id=workbench_id)
        )

        assert (await finished(client, recreate)).state is OperationState.SUCCEEDED
        first, second = runtime.created
        assert first.command == (
            "workbench",
            "--listen",
            "0.0.0.0:8787",
            "--workspace-source",
            "https://github.com/owner/cec-backend.git",
        )
        assert (
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"kinby-{workbench_id}-workspace",
                destination="/instance/workspace",
                writable=True,
            )
            in first.storage
        )
        assert (second.command, second.storage) == (first.command, first.storage)

    asyncio.run(scenario())


def test_a_workbench_without_a_source_keeps_a_plain_folder(tmp_path):
    runtime = FakeRuntime()
    client = hub_client(hub_at(tmp_path / "hub", runtime=runtime))

    async def scenario() -> None:
        await created(client, LANDING)

        [container] = runtime.created
        assert container.command == ("workbench", "--listen", "0.0.0.0:8787")

    asyncio.run(scenario())


def test_a_workbench_record_holds_named_lists_of_repository_commands_each_replaced_whole(
    tmp_path,
):
    client = hub_client(hub_at(tmp_path / "hub"))

    async def scenario() -> None:
        workbench_id = (await created(client)).workbench_id
        fresh = await got(client, workbench_id)

        await _commands_set(client, workbench_id, "setup", ["uv sync", "bun install"])
        await _commands_set(client, workbench_id, "checks", ["bun run check"])
        replaced = await _commands_set(client, workbench_id, "setup", ["uv sync --frozen"])

        assert fresh.repository_commands == []
        assert replaced.repository_commands == [
            RepositoryCommandList(name="checks", commands=["bun run check"]),
            RepositoryCommandList(name="setup", commands=["uv sync --frozen"]),
        ]
        assert (await got(client, workbench_id)) == replaced

    asyncio.run(scenario())


def test_a_command_list_needs_a_list_name_and_commands_that_are_not_blank(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = (await created(client)).workbench_id

        answers = [
            await hub.dispatcher.dispatch(
                WORKBENCH_COMMANDS_SET.name,
                {"workbench_id": str(workbench_id), "name": name, "commands": commands},
                HUB_SCOPES,
            )
            for name, commands in (("Set Up", ["uv sync"]), ("setup", ["  "]))
        ]

        for answer in answers:
            assert isinstance(answer, ErrorEnvelope), answer
            assert answer.code is ErrorCode.INVALID_ARGUMENT
        assert (await got(client, workbench_id)).repository_commands == []

    asyncio.run(scenario())


def test_a_workbench_picks_and_unpicks_library_skills_into_its_directory(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = (await created(client)).workbench_id
        directory = hub.instances_directory / str(workbench_id)

        picked = await _pick(client, workbench_id, "tdd", "unslop")
        copied = _files(directory / "skills" / "tdd")
        unpicked = await _pick(client, workbench_id, "unslop")

        assert picked.picks == [TDD, UNSLOP]
        assert copied == _files(SHIPPED_SKILLS / "tdd")
        assert unpicked.picks == [UNSLOP]
        assert not (directory / "skills" / "tdd").exists()
        assert await _picks(client, workbench_id) == unpicked

    asyncio.run(scenario())


def test_a_workbench_picks_reach_its_container_through_its_directory(tmp_path):
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = (await created(client)).workbench_id

        await _pick(client, workbench_id, "tdd")

        [container] = runtime.created
        directory = hub.instances_directory / str(workbench_id)
        [mounted] = [item for item in container.storage if item.destination == "/instance"]
        assert Path(mounted.source) == directory
        assert (directory / "skills" / "tdd" / "SKILL.md").is_file()

    asyncio.run(scenario())


def test_two_workbenches_hold_their_own_command_lists_and_picks(tmp_path):
    client = hub_client(hub_at(tmp_path / "hub"))

    async def scenario() -> None:
        backend = (await created(client)).workbench_id
        landing = (await created(client, LANDING)).workbench_id

        await _commands_set(client, backend, "checks", ["uv run pytest"])
        await _commands_set(client, landing, "checks", ["pnpm test"])
        await _pick(client, backend, "tdd")
        await _pick(client, landing, "unslop")

        assert (await got(client, backend)).repository_commands == [
            RepositoryCommandList(name="checks", commands=["uv run pytest"])
        ]
        assert (await got(client, landing)).repository_commands == [
            RepositoryCommandList(name="checks", commands=["pnpm test"])
        ]
        assert (await _picks(client, backend)).picks == [TDD]
        assert (await _picks(client, landing)).picks == [UNSLOP]

    asyncio.run(scenario())
