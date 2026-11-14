"""`kinby hub read` reads lines, workbenches and a line's runs from the home instance."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from kinby.cli import main
from kinby.contracts import FactoryRunStatus
from tests.test_factory_runs import settled, started
from tests.test_hub import FakeControl, FakeRuntime, hub_client
from tests.test_hub_home_reads import as_the_home_instance
from tests.test_hub_line_runs import LANDING, _handed_in, _hub, _line
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url

type Reads = Callable[..., Any]


def _read_lines(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys, reads: Reads) -> None:
    """Set up two tickets lines, hand each a work item, and run *reads* as the home instance."""
    control, runtime = FakeControl(), FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def kinby(*arguments: str) -> Any:
        assert await asyncio.to_thread(main, ["hub", "read", *arguments]) == 0
        return json.loads(capsys.readouterr().out)

    async def scenario() -> None:
        recovery = await hub.recover()
        assert recovery.home is not None
        home = await started(client, runtime, recovery.home)
        cec, landing = await _line(client, runtime), await _line(client, runtime, LANDING)
        for line, issue in ((cec, 12), (landing, 13)):
            await settled(hub, (await _handed_in(client, line, issue)).run_id)
        async with served(hub) as address:
            as_the_home_instance(monkeypatch, url(address, f"/instances/{home}/hub"))
            monkeypatch.setenv("KINBY_CONTROL_TOKEN", control_token(hub, str(home)))
            await reads(kinby, cec, landing)

    asyncio.run(scenario())
    hub.close()


def test_the_home_instance_lists_lines_and_workbenches_and_gets_one_of_each(
    tmp_path, monkeypatch, capsys
):
    async def reads(kinby, cec, landing) -> None:
        lines = (await kinby("line.list"))["lines"]
        line = await kinby("line.get", f"line_id={cec.line_id}")
        workbenches = (await kinby("workbench.list"))["workbenches"]
        workbench = await kinby("workbench.get", f"workbench_id={cec.workbench_id}")

        assert [each["line_id"] for each in lines] == [str(cec.line_id), str(landing.line_id)]
        assert (line["factory"], line["workbench_id"]) == ("tickets", str(cec.workbench_id))
        assert [each["name"] for each in workbenches] == ["cec-backend", "landing"]
        assert workbench["repository_commands"] == [
            {"name": "checks", "commands": ["uv run pytest"]}
        ]

    _read_lines(tmp_path, monkeypatch, capsys, reads)


def test_the_home_instance_lists_a_lines_runs_by_the_line_alone_and_gets_one_with_its_attempts(
    tmp_path, monkeypatch, capsys
):
    async def reads(kinby, cec, landing) -> None:
        runs = (await kinby("factory.run.list", f"line_id={landing.line_id}"))["runs"]
        detail = await kinby("factory.run.get", f"run_id={runs[0]['run_id']}")

        assert [(run["line_id"], run["work_item"]) for run in runs] == [
            (str(landing.line_id), {"issue": 13})
        ]
        assert detail["run"]["status"] == FactoryRunStatus.DONE
        assert [(each["step"], each["ending"]) for each in detail["attempts"]] == [
            ("test", "clean"),
            ("label", "clean"),
        ]

    _read_lines(tmp_path, monkeypatch, capsys, reads)
