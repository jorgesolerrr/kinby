"""A turn keeps a view with the view tools, and a client reads it with view.list, view.get and
view.subscribe."""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import JsonValue

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Event,
    RoutineGrant,
    RoutineName,
    Scope,
    ThreadCreateResult,
    ToolGated,
    ToolResult,
    View,
    ViewChange,
    ViewChanged,
    ViewListResult,
    is_turn_closing,
)
from kinby.core.dispatcher import Dispatcher
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import ParkedTurn, TurnOutcome
from kinby.instance import Instance, load_instance
from kinby.instance.layout import VIEWS_DIR
from kinby.instance.routine_grants import RoutineGrants
from kinby.instance.views import VIEW_ERROR_CAP_CHARS, VIEW_ERRORS_CAP, write_view
from kinby.plugins import ToolContext
from kinby.plugins.defaults.files import read
from tests.helpers import thread_events
from tests.test_delegated_runs import call, fire_routine, runtime
from tests.test_gate import ScriptedModel, _instance, _run_turn
from tests.test_routine_grants import next_stop
from tests.test_routines import instance_at, routine_file

PAGE = """<h1>Applications</h1>
<ul id="postings"></ul>
<script>
  for (const posting of kinby.data.postings) {
    document.getElementById("postings").append(Object.assign(document.createElement("li"), {
      textContent: posting.company,
    }))
  }
</script>
"""
DATA: dict[str, JsonValue] = {
    "heading": "Applications",
    "postings": [{"company": "Acme"}, {"company": "Globex"}],
    "url": "https://jobs.example",
}


def calling(*calls: tuple[str, dict[str, object]]) -> ScriptedModel:
    """A model that calls one view tool per turn, then says it is done."""
    responses: list[AIMessageChunk] = []
    for number, (name, arguments) in enumerate(calls):
        responses += [
            AIMessageChunk(
                content="",
                tool_calls=[
                    {"name": name, "args": arguments, "id": f"view-{number}", "type": "tool_call"}
                ],
            ),
            AIMessageChunk(content="Done"),
        ]
    return ScriptedModel(responses)


def writing(*writes: dict[str, object]) -> ScriptedModel:
    """A model that writes one view per turn, then says it is done."""
    return calling(*(("view_write", arguments) for arguments in writes))


def board(page: object = PAGE, data: object = DATA) -> dict[str, object]:
    return {"id": "applications", "title": "Applications", "page": page, "data": data}


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
        assert (view.title, view.page, view.data) == ("Applications", PAGE, DATA)
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


@pytest.mark.parametrize(
    "written",
    [board(data={"notes": "x" * 1_000_000}), board(page="<p>" + "x" * 1_000_000 + "</p>")],
    ids=["data", "page"],
)
def test_a_view_past_one_megabyte_is_refused(tmp_path: Path, written: dict[str, object]) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path), writing(written))

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


CATALOGUE_VIEW = {
    "id": "board",
    "title": "Job board",
    "updated_at": "2026-10-25T09:30:00Z",
    "spec": {"components": [{"id": "root", "component": "Text", "text": {"path": "/note"}}]},
    "data": {"note": "Three open applications."},
}


def catalogue_view(instance: Instance) -> None:
    """A view an older kinby wrote for the view catalogue: a spec and no page."""
    views = instance.path / VIEWS_DIR
    views.mkdir(exist_ok=True)
    (views / "board.json").write_text(json.dumps(CATALOGUE_VIEW), encoding="utf-8")


def test_a_view_written_for_the_view_catalogue_is_listed_with_no_page_and_its_data(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        catalogue_view(instance)
        dispatcher = runtime(instance, calling(("view_read", {"id": "board"})))

        events = await turn(dispatcher)
        view = await call(dispatcher, "view.get", id="board")

        assert await listed(dispatcher) == ["board"]
        assert isinstance(view, View)
        assert (view.title, view.page, view.data) == ("Job board", None, CATALOGUE_VIEW["data"])
        [result] = tool_results(events)
        assert not result.error
        assert "Three open applications." in result.output

    asyncio.run(scenario())


def test_writing_over_a_view_written_for_the_view_catalogue_gives_it_a_page(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        catalogue_view(instance)
        dispatcher = runtime(instance, writing({**board(), "id": "board"}))

        events = await turn(dispatcher)
        view = await call(dispatcher, "view.get", id="board")

        assert [change.change for change in changes(events)] == [ViewChange.CHANGED]
        assert isinstance(view, View)
        assert (view.page, view.data) == (PAGE, DATA)

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


def patch(*ops: dict[str, object]) -> tuple[str, dict[str, object]]:
    return ("view_patch", {"id": "applications", "ops": list(ops)})


ADD_INITECH: dict[str, object] = {
    "op": "add",
    "path": "/data/postings/-",
    "value": {"company": "Initech"},
}


def seeded(instance: Instance) -> None:
    """The applications view, as an earlier turn wrote it."""
    write_view(instance, "applications", "Applications", PAGE, DATA)


@pytest.mark.parametrize(
    ("mode", "action"),
    [("full-access", "allow"), ("auto", "allow"), ("ask", "allow"), ("read-only", "deny")],
)
def test_a_users_turn_writes_a_page_unasked_in_every_mode_but_read_only(
    tmp_path: Path, mode: str, action: str
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        permissions(instance, f'mode = "{mode}"\n')
        runner = LangGraphRunner(instance, model_factory=lambda _: writing(board()))

        result, payloads = await _run_turn(runner)

        assert isinstance(result, TurnOutcome)
        gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
        assert (gated.action.value, gated.rule) == (action, "view.page")
        written = [payload for payload in payloads if isinstance(payload, ViewChanged)]
        assert len(written) == (1 if action == "allow" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "action"),
    [("full-access", "allow"), ("auto", "allow"), ("ask", "allow"), ("read-only", "deny")],
)
def test_a_view_patch_runs_unasked_in_every_mode_but_read_only(
    tmp_path: Path, mode: str, action: str
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        seeded(instance)
        permissions(instance, f'mode = "{mode}"\n')
        runner = LangGraphRunner(instance, model_factory=lambda _: calling(patch(ADD_INITECH)))

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


async def routine_stop(dispatcher: Dispatcher) -> Event:
    """Run the news routine, and return the approval it parks on or the event that closes it."""
    accepted = await call(dispatcher, "routine.run", name="news")
    assert isinstance(accepted, AcceptedResult), accepted
    return await next_stop(dispatcher, accepted.thread_id)


@pytest.mark.parametrize("mode", ["ask", "auto"])
def test_a_routine_turn_asks_before_it_writes_a_page(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        seeded(instance)
        routine_file(instance, f"description: News\nmode: {mode}")
        dispatcher = runtime(instance, writing(board(page="<h1>Sign in</h1>")))

        stop = await routine_stop(dispatcher)
        view = await call(dispatcher, "view.get", id="applications")

        assert isinstance(stop.payload, ApprovalRequested)
        assert (stop.payload.name, stop.payload.rule) == ("view_write", f"mode.{mode}.write")
        assert isinstance(view, View)
        assert view.page == PAGE

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["ask", "auto"])
def test_a_routine_turn_patches_a_views_data_without_parking(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        seeded(instance)
        routine_file(instance, f"description: News\nmode: {mode}")
        dispatcher = runtime(instance, calling(patch(ADD_INITECH)))

        events = await fire_routine(dispatcher)

        assert not any(isinstance(event.payload, ApprovalRequested) for event in events)
        assert [change.change for change in changes(events)] == [ViewChange.CHANGED]

    asyncio.run(scenario())


def test_a_routine_allowed_view_write_writes_a_page_unasked(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nmode: ask")
        instance.manifest.state_dir.mkdir(exist_ok=True)
        RoutineGrants(instance.manifest.state_dir).grant(
            RoutineGrant(
                routine=RoutineName("news"),
                tool="view_write",
                granted_at=datetime.now(UTC),
                approval_id=uuid4(),
            )
        )
        dispatcher = runtime(instance, writing(board()))

        events = await fire_routine(dispatcher)

        assert not any(isinstance(event.payload, ApprovalRequested) for event in events)
        assert [change.change for change in changes(events)] == [ViewChange.CREATED]

    asyncio.run(scenario())


def test_a_routine_whose_code_step_is_view_patch_records_the_change(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        seeded(instance)
        arguments = json.dumps({"id": "applications", "ops": [ADD_INITECH]})
        routine_file(instance, f"description: News\nrun: view_patch\narguments: {arguments}")
        dispatcher = runtime(instance)

        events = await fire_routine(dispatcher)

        assert [change.change for change in changes(events)] == [ViewChange.CHANGED]

    asyncio.run(scenario())


async def snapshots(dispatcher: Dispatcher) -> AsyncGenerator[ContractModel]:
    stream = await dispatcher.subscribe(
        "view.subscribe", {"id": "applications"}, {Scope.INSTANCE_READ}
    )
    assert not isinstance(stream, ErrorEnvelope), stream
    return stream.items


def test_a_subscriber_gets_the_view_then_each_patch_a_turn_makes(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = runtime(
            instance_at(tmp_path), calling(("view_write", board()), patch(ADD_INITECH))
        )
        written = await turn(dispatcher)
        items = await snapshots(dispatcher)

        async with asyncio.timeout(5):
            first = await anext(items)
            patched = await turn(dispatcher)
            second = await anext(items)
        await items.aclose()

        assert isinstance(first, View)
        assert first.data == DATA
        assert [result.error for result in tool_results(patched)] == [False]
        assert isinstance(second, View)
        assert second.data["postings"] == [
            {"company": "Acme"},
            {"company": "Globex"},
            {"company": "Initech"},
        ]
        assert second.updated_at > first.updated_at
        assert [change.change for change in changes(written) + changes(patched)] == [
            ViewChange.CREATED,
            ViewChange.CHANGED,
        ]

    asyncio.run(scenario())


def test_a_patch_changes_the_title_and_the_data_and_keeps_the_page(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = runtime(
            instance_at(tmp_path),
            calling(
                ("view_write", board()),
                patch(ADD_INITECH, {"op": "replace", "path": "/title", "value": "Job board"}),
            ),
        )

        await turn(dispatcher)
        events = await turn(dispatcher)
        view = await call(dispatcher, "view.get", id="applications")

        assert [result.error for result in tool_results(events)] == [False]
        assert changes(events) == [
            ViewChanged(view="applications", title="Job board", change=ViewChange.CHANGED)
        ]
        assert isinstance(view, View)
        assert (view.title, view.page) == ("Job board", PAGE)
        assert view.data["postings"] == [
            {"company": "Acme"},
            {"company": "Globex"},
            {"company": "Initech"},
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("ops", "message"),
    [
        ([{"op": "replace", "path": "/page", "value": "<h1>Sign in</h1>"}], "view_write"),
        ([{"op": "copy", "from": "/page", "path": "/data/page"}], "view_write"),
        ([{"op": "remove", "path": "/title"}], "title"),
        ([{"op": "add", "path": "/data/notes", "value": "x" * 1_000_000}], "capped at 1 MB"),
        ([{"op": "remove", "path": "/data/missing"}], "missing"),
    ],
)
def test_a_patch_that_fails_is_a_tool_error_and_leaves_the_view(
    tmp_path: Path, ops: list[dict[str, object]], message: str
) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path), calling(("view_write", board()), patch(*ops)))
        await turn(dispatcher)
        before = await call(dispatcher, "view.get", id="applications")

        events = await turn(dispatcher)
        after = await call(dispatcher, "view.get", id="applications")

        [result] = tool_results(events)
        assert result.error
        assert message in result.output
        assert changes(events) == []
        assert after == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "op",
    [
        {"op": "append", "path": "/data/postings/-", "value": {}},
        {"op": "add", "path": "/data/postings/-"},
        {"op": "move", "path": "/data/moved"},
        {"op": "remove", "path": "data/postings"},
        {"op": "remove", "path": "/data/postings", "value": 1},
        "add /data/postings/-",
    ],
)
def test_a_patch_with_an_invalid_operation_is_a_tool_error_and_leaves_the_view(
    tmp_path: Path, op: object
) -> None:
    async def scenario() -> None:
        dispatcher = runtime(
            instance_at(tmp_path),
            calling(("view_write", board()), ("view_patch", {"id": "applications", "ops": [op]})),
        )
        await turn(dispatcher)
        before = await call(dispatcher, "view.get", id="applications")

        events = await turn(dispatcher)
        after = await call(dispatcher, "view.get", id="applications")

        [result] = tool_results(events)
        assert result.error
        assert changes(events) == []
        assert after == before

    asyncio.run(scenario())


def test_subscribing_to_a_view_the_instance_does_not_have_is_not_found(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path))

        missing = await dispatcher.subscribe("view.subscribe", {"id": "applications"}, set(Scope))

        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


DELETE: tuple[str, dict[str, object]] = ("view_delete", {"id": "applications"})


@pytest.mark.parametrize(
    ("mode", "action"), [("ask", "ask"), ("auto", "ask"), ("read-only", "deny")]
)
def test_a_view_delete_asks_in_ask_and_auto_mode_and_is_denied_in_read_only(
    tmp_path: Path, mode: str, action: str
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        permissions(instance, f'mode = "{mode}"\n')
        runner = LangGraphRunner(instance, model_factory=lambda _: calling(DELETE))

        result, payloads = await _run_turn(runner)

        if action == "ask":
            assert isinstance(result, ParkedTurn)
            approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
            assert approval.rule == f"mode.{mode}.write"
        else:
            gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
            assert (gated.action.value, gated.rule) == ("deny", "mode.read-only.write")

    asyncio.run(scenario())


async def approved_turn(dispatcher: Dispatcher) -> list[Event]:
    """Start a turn in a new thread, approve each call it asks for, and return its events once it
    closes."""
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
            if isinstance(event.payload, ApprovalRequested):
                approval_id = event.payload.approval_id
                await call(
                    dispatcher,
                    "thread.approval.respond",
                    thread_id=created.id,
                    approval_id=approval_id,
                    decision="approve",
                )
            if is_turn_closing(event.payload):
                break
    await stream.aclose()
    return events


def test_an_approved_view_delete_removes_the_view_and_ends_its_subscription(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path), calling(("view_write", board()), DELETE))
        await turn(dispatcher)
        items = await snapshots(dispatcher)

        async with asyncio.timeout(5):
            await anext(items)
            events = await approved_turn(dispatcher)
            remaining = [item async for item in items]

        assert [result.error for result in tool_results(events)] == [False]
        assert changes(events) == [
            ViewChanged(view="applications", title="Applications", change=ViewChange.DELETED)
        ]
        assert remaining == []
        assert await listed(dispatcher) == []

    asyncio.run(scenario())


def test_a_client_deletes_a_view_which_ends_its_subscription_and_records_no_event(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path), writing(board()))
        await turn(dispatcher)
        threads = await call(dispatcher, "thread.list")
        items = await snapshots(dispatcher)

        async with asyncio.timeout(5):
            await anext(items)
            deleted = await call(dispatcher, "view.delete", id="applications")
            remaining = [item async for item in items]

        assert deleted == ViewListResult(views=[])
        assert remaining == []
        assert await listed(dispatcher) == []
        assert await call(dispatcher, "thread.list") == threads

    asyncio.run(scenario())


def test_deleting_a_view_the_instance_does_not_have_is_not_found(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path))

        missing = await call(dispatcher, "view.delete", id="applications")

        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


ERRORS = ["Uncaught TypeError: kinby.data.posting is undefined"]


def test_a_client_saves_a_pages_errors_without_changing_the_view(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        seeded(instance)
        dispatcher = runtime(instance)
        before = await call(dispatcher, "view.get", id="applications")
        threads = await call(dispatcher, "thread.list")

        saved = await call(dispatcher, "view.errors.set", id="applications", errors=ERRORS)
        view = await call(dispatcher, "view.get", id="applications")

        assert isinstance(before, View)
        assert before.errors == []
        assert saved == view
        assert isinstance(view, View)
        assert view.errors == ERRORS
        assert view.model_dump(exclude={"errors"}) == before.model_dump(exclude={"errors"})
        assert await call(dispatcher, "thread.list") == threads

    asyncio.run(scenario())


def test_view_read_returns_the_errors_the_page_raised(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        seeded(instance)
        dispatcher = runtime(instance, calling(("view_read", {"id": "applications"})))
        await call(dispatcher, "view.errors.set", id="applications", errors=ERRORS)

        events = await turn(dispatcher)

        [result] = tool_results(events)
        assert not result.error
        assert View.model_validate_json(result.output).errors == ERRORS

    asyncio.run(scenario())


def test_a_view_keeps_its_first_errors_each_cut_to_length(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        seeded(instance)
        dispatcher = runtime(instance)
        raised = [f"Error {number}: " + "x" * 5_000 for number in range(50)]

        view = await call(dispatcher, "view.errors.set", id="applications", errors=raised)

        assert isinstance(view, View)
        assert view.errors == [error[:VIEW_ERROR_CAP_CHARS] for error in raised[:VIEW_ERRORS_CAP]]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [("view_write", board()), patch(ADD_INITECH), ("view_write", board(page="<h1>Board</h1>"))],
    ids=["same-page", "data", "page"],
)
def test_the_next_write_of_the_view_clears_its_errors(
    tmp_path: Path, change: tuple[str, dict[str, object]]
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        seeded(instance)
        dispatcher = runtime(instance, calling(change))
        await call(dispatcher, "view.errors.set", id="applications", errors=ERRORS)

        events = await turn(dispatcher)
        view = await call(dispatcher, "view.get", id="applications")

        assert [result.error for result in tool_results(events)] == [False]
        assert isinstance(view, View)
        assert view.errors == []

    asyncio.run(scenario())


def test_saving_errors_for_a_view_the_instance_does_not_have_is_not_found(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = runtime(instance_at(tmp_path))

        missing = await call(dispatcher, "view.errors.set", id="applications", errors=ERRORS)

        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND
        assert await listed(dispatcher) == []

    asyncio.run(scenario())
