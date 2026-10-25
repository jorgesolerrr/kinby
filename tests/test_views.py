"""A turn keeps a view with the view tools, and a client reads it with view.list and view.get."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Scope,
    ThreadCreateResult,
    ToolGated,
    ToolResult,
    View,
    ViewChange,
    ViewChanged,
    ViewListResult,
    ViewSpec,
    is_turn_closing,
)
from kinby.core import LangGraphRunner
from kinby.core.dispatcher import Dispatcher
from kinby.core.turns import ParkedTurn, TurnOutcome
from kinby.instance import Instance, load_instance
from kinby.plugins import ToolContext
from kinby.plugins.defaults.files import read
from tests.helpers import thread_events
from tests.test_delegated_runs import call, fire_routine, runtime
from tests.test_gate import ScriptedModel, _instance, _run_turn
from tests.test_routines import instance_at, routine_file

BOARD = {
    "components": [
        {
            "id": "root",
            "component": "Card",
            "title": {"path": "/heading"},
            "children": ["postings", "source"],
        },
        {
            "id": "postings",
            "component": "Card",
            "children": {"path": "/postings", "template": "posting"},
        },
        {"id": "posting", "component": "Text", "text": {"path": "/company"}},
        {
            "id": "source",
            "component": "Link",
            "label": "All postings",
            "href": {"path": "/url"},
        },
    ]
}
DATA = {
    "heading": "Applications",
    "postings": [{"company": "Acme"}, {"company": "Globex"}],
    "url": "https://jobs.example",
}


def writing(*writes: dict[str, object]) -> ScriptedModel:
    """A model that writes one view per turn, then says it is done."""
    responses: list[AIMessageChunk] = []
    for number, arguments in enumerate(writes):
        responses += [
            AIMessageChunk(
                content="",
                tool_calls=[
                    {
                        "name": "view_write",
                        "args": arguments,
                        "id": f"view-{number}",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessageChunk(content="Done"),
        ]
    return ScriptedModel(responses)


def board(spec: object = BOARD, data: object = DATA) -> dict[str, object]:
    return {"id": "applications", "title": "Applications", "spec": spec, "data": data}


async def turn(dispatcher: Dispatcher) -> list[Event]:
    """Start a turn in a new thread, and return its events once it closes."""
    created = await call(dispatcher, "thread.create")
    assert isinstance(created, ThreadCreateResult)
    accepted = await call(dispatcher, "thread.turn.start", thread_id=created.id, message="Board")
    assert isinstance(accepted, AcceptedResult), accepted
    events: list[Event] = []
    stream = await thread_events(dispatcher, {"thread_id": created.id}, set(Scope))
    async with asyncio.timeout(5):
        async for event in stream:
            assert isinstance(event, Event)
            events.append(event)
            if is_turn_closing(event.payload):
                break
    await stream.aclose()
    return events


def tool_results(events: list[Event]) -> list[ToolResult]:
    return [event.payload for event in events if isinstance(event.payload, ToolResult)]


def changes(events: list[Event]) -> list[ViewChanged]:
    return [event.payload for event in events if isinstance(event.payload, ViewChanged)]


async def listed(dispatcher: Dispatcher) -> list[str]:
    result = await call(dispatcher, "view.list")
    assert isinstance(result, ViewListResult)
    return [view.id for view in result.views]


def test_a_turn_writes_a_view_that_a_client_lists_and_gets(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path), writing(board()))

        events = await turn(dispatcher)
        result = await call(dispatcher, "view.list")
        view = await call(dispatcher, "view.get", id="applications")

        assert [result.error for result in tool_results(events)] == [False]
        assert changes(events) == [
            ViewChanged(view="applications", title="Applications", change=ViewChange.CREATED)
        ]
        assert isinstance(result, ViewListResult)
        assert [(summary.id, summary.title) for summary in result.views] == [
            ("applications", "Applications")
        ]
        assert isinstance(view, View)
        assert (view.title, view.spec, view.data) == (
            "Applications",
            ViewSpec.model_validate(BOARD),
            DATA,
        )
        assert view.updated_at == result.views[0].updated_at

    asyncio.run(scenario())


def test_writing_a_view_again_records_it_changed(tmp_path: Path) -> None:
    async def scenario() -> None:
        renamed = {**board(), "title": "Job board"}
        dispatcher = runtime(instance_at(tmp_path), writing(board(), renamed))

        await turn(dispatcher)
        events = await turn(dispatcher)
        view = await call(dispatcher, "view.get", id="applications")

        assert changes(events) == [
            ViewChanged(view="applications", title="Job board", change=ViewChange.CHANGED)
        ]
        assert isinstance(view, View)
        assert view.title == "Job board"

    asyncio.run(scenario())


def _text(component: str = "root") -> dict[str, object]:
    return {"id": component, "component": "Text", "text": "Hi"}


@pytest.mark.parametrize(
    ("components", "message"),
    [
        ([_text("intro")], 'One component must have the id "root".'),
        (
            [{"id": "root", "component": "Card", "children": ["missing"]}],
            'Component "root" names components that do not exist: missing.',
        ),
        (
            [
                {
                    "id": "root",
                    "component": "Card",
                    "children": {"path": "/items", "template": "row"},
                }
            ],
            'Component "root" names components that do not exist: row.',
        ),
        ([_text(), _text()], "Component ids must be unique; repeated: root."),
        (
            [
                {"id": "root", "component": "Card", "children": ["inner"]},
                {"id": "inner", "component": "Card", "children": ["root"]},
            ],
            "Components must not contain themselves: root -> inner -> root.",
        ),
        ([{"id": "root", "component": "Marquee"}], "Input tag 'Marquee'"),
    ],
)
def test_a_spec_the_catalogue_refuses_is_a_tool_error_and_writes_nothing(
    tmp_path: Path, components: list[dict[str, object]], message: str
) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path), writing(board({"components": components})))

        events = await turn(dispatcher)

        [result] = tool_results(events)
        assert result.error
        assert message in result.output
        assert changes(events) == []
        assert await listed(dispatcher) == []

    asyncio.run(scenario())


def test_a_view_past_one_megabyte_is_refused(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path), writing(board(data={"notes": "x" * 1_000_000})))

        events = await turn(dispatcher)

        [result] = tool_results(events)
        assert result.error
        assert "capped at 1 MB" in result.output
        assert changes(events) == []
        assert await listed(dispatcher) == []

    asyncio.run(scenario())


def test_a_view_outlives_the_instance_rebuilt_on_its_directory(tmp_path: Path) -> None:
    async def scenario() -> None:
        await turn(runtime(instance_at(tmp_path), writing(board())))

        rebuilt = runtime(load_instance(tmp_path))
        view = await call(rebuilt, "view.get", id="applications")

        assert isinstance(view, View)
        assert view.data == DATA

    asyncio.run(scenario())


def test_getting_a_view_the_instance_does_not_have_is_not_found(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path))

        missing = await call(dispatcher, "view.get", id="applications")

        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_workspace_file_tools_cannot_reach_views(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        await turn(runtime(instance, writing(board())))
        context = ToolContext(instance=instance, thread_id=uuid4())

        with pytest.raises(ValueError, match="outside the workspace"):
            await read.ainvoke({"path": "../views/applications.json"}, context)

    asyncio.run(scenario())


def permissions(instance: Instance, text: str) -> None:
    (instance.path / "permissions.toml").write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("mode", "action"),
    [("full-access", "allow"), ("auto", "allow"), ("ask", "allow"), ("read-only", "deny")],
)
def test_a_view_write_runs_unasked_in_every_mode_but_read_only(
    tmp_path: Path, mode: str, action: str
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        permissions(instance, f'mode = "{mode}"\n')
        runner = LangGraphRunner(instance, model_factory=lambda _: writing(board()))

        result, payloads = await _run_turn(runner)

        assert isinstance(result, TurnOutcome)
        gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
        assert (gated.action.value, gated.rule) == (action, "view")
        written = [payload for payload in payloads if isinstance(payload, ViewChanged)]
        assert len(written) == (1 if action == "allow" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "rule"), [("read-only", "allow"), ("full-access", "deny"), ("full-access", "ask")]
)
def test_a_permissions_rule_for_view_write_overrides_its_default(
    tmp_path: Path, mode: str, rule: str
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        permissions(instance, f'mode = "{mode}"\n\n[tools]\nview_write = "{rule}"\n')
        runner = LangGraphRunner(instance, model_factory=lambda _: writing(board()))

        result, payloads = await _run_turn(runner)

        if rule == "ask":
            assert isinstance(result, ParkedTurn)
            approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
            assert approval.rule == "tools.view_write"
        else:
            gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
            assert (gated.action.value, gated.rule) == (rule, "tools.view_write")

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["ask", "auto"])
def test_a_routine_turn_writes_a_view_without_parking(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, f"description: News\nmode: {mode}")
        dispatcher = runtime(instance, writing(board()))

        events = await fire_routine(dispatcher)

        assert not any(isinstance(event.payload, ApprovalRequested) for event in events)
        assert [change.change for change in changes(events)] == [ViewChange.CREATED]
        assert await listed(dispatcher) == ["applications"]

    asyncio.run(scenario())
