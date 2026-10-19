import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from kinby.cli import main
from kinby.core.runtime_lock import runtime_lock
from kinby.instance import Instance, init_instance, load_instance
from kinby.memory import Episode, Fact, GraphStore, NodeId, RecappedTurn, memory_tools
from kinby.plugins import ToolContext

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
