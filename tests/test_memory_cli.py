import asyncio
import json
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from kinby.cli import main
from kinby.contracts import MemoryRecalled
from kinby.core.runtime_lock import runtime_lock
from kinby.instance import Instance, init_instance, load_instance
from kinby.instance.database import open_database
from kinby.memory import Episode, Fact, GraphStore, NodeId, RecappedTurn, memory_tools
from kinby.memory.facade import RecappedRun
from kinby.memory.tools import hits_json, open_memory
from kinby.plugins import ToolContext
from tests.test_hub_factories import write_factory
from tests.test_hub_line_intake import Owned, _started_workbench, owned
from tests.test_hub_routing import control_token
from tests.test_hub_workbench_memory import CHORES, _lessons, _run

_THREAD = UUID("11111111-1111-1111-1111-111111111111")
_FACT = Fact(
    node=NodeId("2026-09-02-deploys-run-from-the-hub"),
    date=date(2026, 9, 2),
    thread=_THREAD,
    description="Deploys run from the hub",
    subjects=("deployment", "hub"),
    body="The hub builds the image and recreates the container.",
)
_EPISODE = Episode(
    node=NodeId("2026-08-30-fixed-the-deployment"),
    date=date(2026, 8, 30),
    recaps=RecappedTurn(thread=_THREAD, turn=UUID("22222222-2222-2222-2222-222222222222")),
    description="Fixed the deployment",
    subjects=("deployment",),
    tools=("bash",),
    body="Found the stale image tag, rebuilt the image, then restarted the container.",
)


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Instance:
    path = tmp_path / "coder"
    init_instance(path)
    memory = GraphStore(path)
    memory.remember(_FACT)
    memory.remember(_EPISODE)
    monkeypatch.setenv("KINBY_INSTANCE", str(path))
    # Outside a factory run's coding session, even when the suite runs inside one.
    for variable in ("KINBY_RUN", "KINBY_STEP", "KINBY_ATTEMPT"):
        monkeypatch.delenv(variable, raising=False)
    return load_instance(path)


def _tool_result(instance: Instance, name: str, arguments: dict[str, str]) -> Any:
    tool = next(tool for tool in memory_tools(GraphStore(instance.path)) if tool.name == name)
    context = ToolContext(instance=instance, thread_id=_THREAD)
    return json.loads(asyncio.run(tool.ainvoke(arguments, context)))


def _printed(capsys: pytest.CaptureFixture[str], *arguments: str) -> Any:
    assert main(["memory", *arguments]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out)


def test_memory_search_prints_the_hits_memory_search_returns(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    printed = _printed(capsys, "search", "deployment")

    assert printed == _tool_result(instance, "memory_search", {"query": "deployment"})
    assert [hit["node"] for hit in printed] == [_FACT.node]


def test_memory_search_with_date_bounds_returns_the_episodes_in_range(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    bounds = {"after": "2026-08-01", "before": "2026-08-31"}

    printed = _printed(
        capsys, "search", "deployment", "--after", bounds["after"], "--before", bounds["before"]
    )

    assert printed == _tool_result(instance, "memory_search", {"query": "deployment", **bounds})
    assert [hit["node"] for hit in printed] == [_EPISODE.node]


def test_memory_open_prints_the_body_memory_open_returns(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    printed = _printed(capsys, "open", _EPISODE.node)

    assert printed == _tool_result(instance, "memory_open", {"node": _EPISODE.node})
    assert printed["body"] == _EPISODE.body


def test_memory_open_of_an_unknown_node_exits_non_zero_and_says_so(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    status = main(["memory", "open", "2026-09-03-never-written"])
    output = capsys.readouterr()

    assert status == 1
    assert output.out == ""
    assert output.err == 'Graph node "2026-09-03-never-written" was not found.\n'


def test_memory_open_of_a_node_with_invalid_frontmatter_exits_non_zero_and_says_so(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = instance.path / "memory" / "graph" / "2026-09-03-broken.md"
    broken.write_text("No frontmatter.\n", encoding="utf-8")

    status = main(["memory", "open", "2026-09-03-broken"])
    output = capsys.readouterr()

    assert status == 1
    assert output.out == ""
    assert output.err == f'Graph node "{broken}" has invalid frontmatter.\n'


def test_memory_commands_run_while_the_instance_serves_and_change_nothing(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    with runtime_lock(instance.manifest.state_dir):
        before = _files(instance.path)
        searched = main(["memory", "search", "deployment", "--instance", str(instance.path)])
        opened = main(["memory", "open", _FACT.node, "--instance", str(instance.path)])
        after = _files(instance.path)

    assert (searched, opened) == (0, 0)
    assert capsys.readouterr().err == ""
    assert after == before


def _files(directory: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def test_memory_open_records_its_recall_for_the_coding_sessions_step(
    instance: Instance, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = uuid4()
    monkeypatch.setenv("KINBY_RUN", str(run_id))
    monkeypatch.setenv("KINBY_STEP", "implement")
    monkeypatch.setenv("KINBY_ATTEMPT", "2")

    _printed(capsys, "open", _FACT.node)

    with closing(open_database(instance.manifest.state_dir)) as db:
        kept = db.execute("SELECT run_id, recall FROM recalls").fetchall()
    assert [(UUID(run), MemoryRecalled.model_validate_json(recall)) for run, recall in kept] == [
        (run_id, MemoryRecalled(nodes=(_FACT.node,), step="implement", attempt=2))
    ]


#: An episode of a run on the cec-backend workbench, in the line owner's memory.
_RUN_EPISODE = Episode(
    node=NodeId("2026-11-02-installed-before-the-checks"),
    date=date(2026, 11, 2),
    recaps=RecappedRun(run=UUID("33333333-3333-3333-3333-333333333333"), generation=1),
    description="Installed before the checks",
    subjects=("install",),
    tools=("bash",),
    body="The checks failed until the install ran.",
    workbench="cec-backend",
)


async def _on_workbench(owned: Owned, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the CLI as an attempt at a step of a run on a workbench, on a line *owned*'s home
    owns."""
    workbench_id = await _started_workbench(owned)
    run = await _run(owned, workbench_id)
    monkeypatch.setenv("KINBY_HUB_URL", f"{owned.address}/instances/{workbench_id}/hub")
    monkeypatch.setenv("KINBY_HUB_SCOPES", "workbench:memory")
    monkeypatch.setenv("KINBY_CONTROL_TOKEN", control_token(owned.hub, str(workbench_id)))
    monkeypatch.setenv("KINBY_RUN", str(run.run_id))
    monkeypatch.setenv("KINBY_STEP", "draft")
    monkeypatch.setenv("KINBY_ATTEMPT", "1")


async def _kinby(*arguments: str) -> int:
    """Run the CLI beside the hub, which keeps serving on this loop."""
    return await asyncio.to_thread(main, list(arguments))


@pytest.fixture
def chores(tmp_path: Path) -> None:
    write_factory(tmp_path / "hub" / "factories" / "chores", {"factory.yaml": CHORES})


@pytest.mark.usefixtures("chores")
def test_on_a_workbench_memory_commands_read_the_line_owners_memory_through_the_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def scenario() -> tuple[dict[str | None, NodeId], list[int]]:
        async with owned(tmp_path, monkeypatch) as hub:
            lessons = _lessons(hub.instance, "cec-backend", "landing", None)
            GraphStore(hub.instance.path).remember(_RUN_EPISODE)
            await _on_workbench(hub, monkeypatch)
            statuses = [
                await _kinby("memory", "search", "install"),
                await _kinby("memory", "open", lessons[None]),
                await _kinby("memory", "open", _RUN_EPISODE.node),
            ]
            return lessons, statuses

    lessons, statuses = asyncio.run(scenario())

    assert statuses == [0, 0, 0]
    searched, opened, episode = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    owner = GraphStore(tmp_path / "home")
    local = owner.recall("install", workbench="cec-backend")
    assert searched == json.loads(hits_json(local))
    assert {hit["node"] for hit in searched} == {lessons["cec-backend"], lessons[None]}
    assert opened == json.loads(open_memory(owner, lessons[None]))
    assert episode == json.loads(open_memory(owner, _RUN_EPISODE.node))


@pytest.mark.usefixtures("chores")
def test_on_a_workbench_whose_line_owner_is_stopped_memory_commands_say_so_and_exit_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def scenario() -> list[int]:
        async with owned(tmp_path, monkeypatch) as hub:
            [lesson] = _lessons(hub.instance, None).values()
            await _on_workbench(hub, monkeypatch)
            del hub.runtime.addresses[str(hub.home)]
            return [
                await _kinby("memory", "search", "install"),
                await _kinby("memory", "open", lesson),
            ]

    statuses = asyncio.run(scenario())

    output = capsys.readouterr()
    assert (statuses, output.out) == ([0, 0], "")
    # The hub serving in this process logs to the same stderr.
    told = [line for line in output.err.splitlines() if line.endswith("Go on without memory.")]
    assert len(told) == 2
    for line in told:
        assert line.startswith(
            'The memory of "home", which owns the line of the run, is unavailable'
        )


@pytest.mark.usefixtures("chores")
def test_on_a_workbench_an_instance_named_on_the_command_line_is_read_locally(
    tmp_path: Path,
    instance: Instance,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def scenario() -> int:
        async with owned(tmp_path, monkeypatch) as hub:
            await _on_workbench(hub, monkeypatch)
            return await _kinby("memory", "search", "deployment", "--instance", str(instance.path))

    assert asyncio.run(scenario()) == 0
    assert [hit["node"] for hit in json.loads(capsys.readouterr().out)] == [_FACT.node]


def test_on_a_workbench_whose_hub_is_unreachable_memory_commands_say_so_and_exit_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KINBY_HUB_URL", "http://127.0.0.1:9/instances/cec/hub")
    monkeypatch.setenv("KINBY_HUB_SCOPES", "workbench:memory")
    monkeypatch.setenv("KINBY_RUN", str(uuid4()))
    monkeypatch.setenv("KINBY_STEP", "implement")
    monkeypatch.setenv("KINBY_ATTEMPT", "1")

    status = main(["memory", "search", "install"])

    output = capsys.readouterr()
    assert (status, output.out) == (0, "")
    assert output.err.startswith("The hub refused the memory lookup: ")
    assert output.err.endswith("Go on without memory.\n")
