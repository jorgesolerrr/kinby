"""`kinby hub read`, `methods`, `help` and `wait`: the home instance reads the hub from bash."""

import asyncio
import json

import pytest

from kinby.cli import main
from kinby.contracts import (
    IMAGE_PREPARE,
    ImagePrepareCommand,
    ImagePrepareResult,
    OperationGetCommand,
    OperationGetResult,
)
from kinby.hub import BuiltImage, ImageSelection
from tests.test_hub import hub_client
from tests.test_hub_home_reads import HomeHub, as_the_home_instance, home_hub


def as_home(monkeypatch: pytest.MonkeyPatch, hub: HomeHub) -> None:
    as_the_home_instance(monkeypatch, hub.route(hub.home))
    monkeypatch.setenv("KINBY_CONTROL_TOKEN", hub.home_token)


async def kinby(*arguments: str) -> int:
    """Run the CLI beside the hub, which keeps serving on this loop."""
    return await asyncio.to_thread(main, list(arguments))


def test_hub_read_prints_a_hub_read_as_compact_json(tmp_path, monkeypatch, capsys):
    async def scenario() -> int:
        async with home_hub(tmp_path) as hub:
            as_home(monkeypatch, hub)
            return await kinby("hub", "read", "instance.list", "removed=false")

    assert asyncio.run(scenario()) == 0
    printed = capsys.readouterr().out
    assert printed.count("\n") == 1
    assert '"manifest_id":"alice"' in printed
    listed = json.loads(printed)
    assert {each["manifest_id"] for each in listed["instances"]} == {"home", "alice"}


def test_hub_read_relays_a_read_to_the_instance_it_names(tmp_path, monkeypatch, capsys):
    async def scenario() -> int:
        async with home_hub(tmp_path) as hub:
            as_home(monkeypatch, hub)
            return await kinby("hub", "read", "routine.list", f"instance_id={hub.alice}")

    assert asyncio.run(scenario()) == 0
    routines = json.loads(capsys.readouterr().out)
    assert [routine["name"] for routine in routines["routines"]] == ["news"]


def test_hub_read_takes_params_as_json(tmp_path, monkeypatch, capsys):
    async def scenario() -> int:
        async with home_hub(tmp_path) as hub:
            as_home(monkeypatch, hub)
            return await kinby(
                "hub",
                "read",
                "routine.read",
                "--params",
                json.dumps({"instance_id": str(hub.alice)}),
                "name=news",
            )

    assert asyncio.run(scenario()) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "news"


def test_hub_read_prints_a_table_for_a_person(tmp_path, monkeypatch, capsys):
    async def scenario() -> int:
        async with home_hub(tmp_path) as hub:
            as_home(monkeypatch, hub)
            return await kinby("hub", "read", "instance.list", "--table")

    assert asyncio.run(scenario()) == 0
    header, *rows = capsys.readouterr().out.splitlines()
    assert header.split()[:2] == ["instance_id", "manifest_id"]
    assert sorted(row.split()[1] for row in rows) == ["alice", "home"]


def test_a_refused_read_exits_nonzero_with_its_code_message_and_fields(
    tmp_path, monkeypatch, capsys
):
    async def scenario() -> int:
        async with home_hub(tmp_path) as hub:
            as_home(monkeypatch, hub)
            return await kinby("hub", "read", "library.git.preview", "address=nowhere")

    assert asyncio.run(scenario()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    # The hub's own access log can land on stderr around the refusal.
    assert (
        'INVALID_ARGUMENT: Some values are invalid. address: "nowhere" is not an address.'
        in captured.err
    )


def test_hub_read_reads_only(monkeypatch, capsys):
    as_the_home_instance(monkeypatch, "http://hub:8080/instances/x/hub")

    assert main(["hub", "read", "instance.remove", "instance_id=x"]) == 1
    assert "kinby hub methods" in capsys.readouterr().err


def test_without_hub_home_the_cli_refuses(monkeypatch, capsys):
    monkeypatch.setenv("KINBY_HUB_URL", "http://hub:8080/instances/x/hub")
    monkeypatch.setenv("KINBY_HUB_SCOPES", "notification:raise")

    assert main(["hub", "read", "instance.list"]) == 1
    assert main(["hub", "wait", "00000000-0000-0000-0000-000000000001"]) == 1
    assert (
        capsys.readouterr().err.splitlines()
        == ["kinby hub read and wait run only in the home instance, which its hub grants hub:home."]
        * 2
    )


def test_hub_wait_returns_once_the_operation_ends(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("kinby.cli.hub_reads.POLL_SECONDS", 0.01)

    async def scenario() -> tuple[int, OperationGetResult]:
        async with home_hub(tmp_path) as hub:
            as_home(monkeypatch, hub)
            images = hub.hub._images
            released = asyncio.Event()
            build, answer = images.build, hub.hub.operation

            async def held_build(selection: ImageSelection) -> BuiltImage:
                await released.wait()
                return await build(selection)

            async def first_answer_releases(command: OperationGetCommand) -> OperationGetResult:
                answered = await answer(command)
                released.set()
                return answered

            monkeypatch.setattr(images, "build", held_build)
            monkeypatch.setattr(hub.hub, "operation", first_answer_releases)
            preparing = await hub_client(hub.hub).call(IMAGE_PREPARE, ImagePrepareCommand())
            assert isinstance(preparing, ImagePrepareResult)
            first = await answer(OperationGetCommand(operation_id=preparing.operation_id))
            return await kinby("hub", "wait", str(preparing.operation_id)), first

    exit_code, first = asyncio.run(scenario())

    assert first.state not in {"succeeded", "failed"}
    assert exit_code == 0, capsys.readouterr()
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"


def test_hub_methods_lists_every_read_and_write_once(capsys):
    assert main(["hub", "methods"]) == 0

    lines = capsys.readouterr().out.splitlines()
    named = {line.split()[0]: " ".join(line.split()[1:]) for line in lines}
    assert len(named) == len(lines)
    assert named["instance.list"] == "hub read"
    assert named["memory.list"] == "relayed read"
    assert named["instance_create"] == "hub write"
    assert "hub.write" not in named
    assert "instance.remove" not in named


def test_hub_help_prints_a_reads_params(capsys):
    assert main(["hub", "help", "memory.list"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "memory.list: a relayed read. Params:"
    assert "  instance_id: UUID, required" in lines
    assert '  query: str = ""' in lines
    assert "  cursor: NodeId | null = null" in lines
    assert "  limit: int = 50" in lines


def test_hub_read_help_prints_the_same_as_hub_help(capsys):
    assert main(["hub", "help", "instance.logs"]) == 0
    helped = capsys.readouterr().out
    assert main(["hub", "read", "instance.logs", "--help"]) == 0

    assert capsys.readouterr().out == helped
    assert "  tail: int | null = 200" in helped.splitlines()


def test_hub_help_prints_a_writes_arguments(capsys):
    assert main(["hub", "help", "instance_lifecycle"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "instance_lifecycle: a hub write. Arguments:",
        "  instance_id: UUID, required",
        "  action: start | stop | recreate, required",
    ]


def test_hub_help_refuses_a_name_that_is_no_read_or_write(capsys):
    assert main(["hub", "help", "instance.remove"]) == 1
    assert "kinby hub methods" in capsys.readouterr().err
