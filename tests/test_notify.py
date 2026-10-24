"""A turn tells the user something with notify, and the scheduler with each routine notice: the
instance records the notification, raises it on its hub route, and raises it again on each
scheduler pass until the hub answers."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    NOTIFICATION_RAISE,
    AcceptedResult,
    ApprovalRequested,
    CallFrame,
    ErrorCode,
    ErrorEnvelope,
    ErrorFrame,
    NotificationAccepted,
    NotificationRaiseCommand,
    NotificationRaised,
    NotificationRefused,
    ResultFrame,
    Scope,
    ToolGated,
    ToolResult,
)
from kinby.core import LangGraphRunner
from kinby.core.dispatcher import ScheduledTurnConfig, TurnConfig, build_dispatcher
from kinby.core.errors import CodeStepFailed
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turns import ParkedTurn, TurnOutcome
from kinby.instance import Instance
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE
from tests.test_gate import ScriptedModel, _instance, _run_turn, _write_store_tool
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import (
    FailingRunner,
    FakeClock,
    call,
    events_for,
    fail_routine,
    runtime,
)

_CAP = "alice raised 20 notifications today, its daily cap. Raise no more until tomorrow."


class RecordingHub:
    """Plays the instance's hub route: records each notification.raise and answers as told."""

    def __init__(self) -> None:
        self.raised: list[NotificationRaiseCommand] = []
        self.down = False
        self.refusal: str | None = None


@asynccontextmanager
async def recording_hub(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[RecordingHub]:
    hub = RecordingHub()

    async def route(request: web.Request) -> web.StreamResponse:
        if hub.down:
            return web.Response(status=503)
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        call = CallFrame.model_validate_json(await socket.receive_str())
        assert call.method == NOTIFICATION_RAISE.name
        hub.raised.append(NotificationRaiseCommand.model_validate(call.params))
        answer = (
            ErrorFrame(
                id=call.id,
                error=ErrorEnvelope(
                    code=ErrorCode.RESOURCE_EXHAUSTED, message=hub.refusal, retryable=False
                ),
            )
            if hub.refusal is not None
            else ResultFrame(id=call.id, result={"notification_id": str(uuid4())})
        )
        await socket.send_str(answer.model_dump_json())
        await socket.close()
        return socket

    application = web.Application()
    application.router.add_get("/hub", route)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        monkeypatch.setenv(HUB_URL_VARIABLE, f"ws://127.0.0.1:{site.port}/hub")
        monkeypatch.setenv(HUB_SCOPES_VARIABLE, Scope.NOTIFICATION_RAISE.value)
        yield hub
    finally:
        await runner.cleanup()


def notifying(options: list[str] | None = None) -> ScriptedModel:
    arguments: dict[str, object] = {"title": "Build broken", "text": "main fails since 9:00."}
    if options is not None:
        arguments["options"] = options
    return ScriptedModel(
        [
            AIMessageChunk(
                content="",
                tool_calls=[
                    {"name": "notify", "args": arguments, "id": "notify-1", "type": "tool_call"}
                ],
            ),
            AIMessageChunk(content="Done"),
        ]
    )


def permissions(instance: Instance, text: str) -> None:
    (instance.path / "permissions.toml").write_text(text, encoding="utf-8")


def tool_result(payloads: list) -> ToolResult:
    return next(payload for payload in payloads if isinstance(payload, ToolResult))


def test_notify_raises_the_notification_on_the_hub_route_and_records_its_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with recording_hub(monkeypatch) as hub:
            runner = LangGraphRunner(
                _instance(tmp_path), model_factory=lambda _: notifying(["Retry", "Ignore"])
            )
            result, payloads = await _run_turn(runner)

        assert isinstance(result, TurnOutcome)
        assert not tool_result(payloads).error
        raised = next(payload for payload in payloads if isinstance(payload, NotificationRaised))
        sequence = payloads.index(raised) + 1
        [command] = hub.raised
        assert command.key == f"{command.thread_id}:{sequence}"
        assert (command.source, command.title, command.text, command.options) == (
            "Untitled thread",
            "Build broken",
            "main fails since 9:00.",
            ["Retry", "Ignore"],
        )
        accepted = [payload for payload in payloads if isinstance(payload, NotificationAccepted)]
        assert [payload.raised for payload in accepted] == [sequence]

    asyncio.run(scenario())


def test_a_hub_out_of_reach_leaves_the_notification_pending_until_a_scheduler_pass_raises_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        routine_file(instance, "description: News")
        log = EventLog(instance.manifest.state_dir)
        runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: notifying())
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            event_log=log,
            turns=ScheduledTurnConfig(
                TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
                SchedulerConfig(instance),
            ),
        )
        scheduler = dispatcher.scheduler
        assert scheduler is not None
        async with recording_hub(monkeypatch) as hub:
            hub.down = True
            accepted = await dispatcher.dispatch("routine.run", {"name": "news"}, set(Scope))
            assert isinstance(accepted, AcceptedResult), accepted
            await scheduler.drain()
            pending = [event.payload for event in log.stored(accepted.thread_id)]
            hub.down = False
            await scheduler.tick()
            await scheduler.tick()
        answered = [event.payload for event in log.stored(accepted.thread_id)]

        assert not tool_result(pending).error
        assert any(isinstance(payload, NotificationRaised) for payload in pending)
        assert not any(isinstance(payload, NotificationAccepted) for payload in pending)
        assert [command.source for command in hub.raised] == ["news"]
        assert isinstance(answered[-1], NotificationAccepted)

    asyncio.run(scenario())


def test_the_daily_cap_reaches_the_model_as_a_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with recording_hub(monkeypatch) as hub:
            hub.refusal = _CAP
            runner = LangGraphRunner(_instance(tmp_path), model_factory=lambda _: notifying())
            _, payloads = await _run_turn(runner)

        result = tool_result(payloads)
        assert result.error
        assert _CAP in result.output
        refused = [payload for payload in payloads if isinstance(payload, NotificationRefused)]
        assert len(refused) == 1
        assert _CAP in refused[0].reason

    asyncio.run(scenario())


def test_an_instance_without_a_hub_url_raises_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = LangGraphRunner(_instance(tmp_path), model_factory=lambda _: notifying())
        _, payloads = await _run_turn(runner)

        assert tool_result(payloads).output == 'Tool "notify" is not available in this turn.'
        assert not any(isinstance(payload, NotificationRaised) for payload in payloads)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "action"),
    [("full-access", "allow"), ("auto", "allow"), ("ask", "allow"), ("read-only", "deny")],
)
def test_notify_runs_unasked_in_every_mode_but_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, action: str
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        permissions(instance, f'mode = "{mode}"\n')
        async with recording_hub(monkeypatch) as hub:
            runner = LangGraphRunner(instance, model_factory=lambda _: notifying())
            result, payloads = await _run_turn(runner)

        assert isinstance(result, TurnOutcome)
        gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
        assert (gated.action.value, gated.rule) == (action, "notify")
        assert len(hub.raised) == (1 if action == "allow" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "rule", "raised"),
    [("read-only", "allow", 1), ("full-access", "deny", 0), ("full-access", "ask", 0)],
)
def test_a_permissions_rule_for_notify_overrides_its_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, rule: str, raised: int
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        permissions(instance, f'mode = "{mode}"\n\n[tools]\nnotify = "{rule}"\n')
        async with recording_hub(monkeypatch) as hub:
            runner = LangGraphRunner(instance, model_factory=lambda _: notifying())
            result, payloads = await _run_turn(runner)

        assert len([command for command in hub.raised if command.approval is None]) == raised
        if rule == "ask":
            assert isinstance(result, ParkedTurn)
            approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
            assert approval.rule == "tools.notify"
        else:
            gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
            assert (gated.action.value, gated.rule) == (rule, "tools.notify")

    asyncio.run(scenario())


def test_a_routine_notice_raises_one_notification_per_failure_streak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nenabled: true")
        runner = FailingRunner()
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), runner)
        async with recording_hub(monkeypatch) as hub:
            failed = await call(dispatcher, "routine.run", name="news")
            await events_for(dispatcher, failed.thread_id)
            await fail_routine(dispatcher, 2)
            await dispatcher.scheduler.tick()
            streak = list(hub.raised)
            runner.result = TurnOutcome()
            await fail_routine(dispatcher, 1)
            runner.result = CodeStepFailed("feed unavailable")
            await fail_routine(dispatcher, 2)
            await dispatcher.scheduler.tick()

        [first] = streak
        assert (first.source, first.title, first.text, first.options) == (
            "news",
            'Routine "news" failed',
            "kinby disables it after 10 failures in a row.",
            [],
        )
        assert first.thread_id == failed.thread_id
        assert len(hub.raised) == 2

    asyncio.run(scenario())


def test_a_routine_disabled_by_the_failure_policy_raises_a_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nenabled: true")
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), FailingRunner())
        async with recording_hub(monkeypatch) as hub:
            await fail_routine(dispatcher, 11)
            await dispatcher.scheduler.tick()

        assert [command.title for command in hub.raised] == [
            'Routine "news" failed',
            'Routine "news" disabled',
        ]
        assert hub.raised[-1].text == "It failed 10 times in a row, so kinby turned it off."
        assert not any("feed unavailable" in command.text for command in hub.raised)

    asyncio.run(scenario())


def test_a_routine_notice_without_a_hub_url_raises_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nenabled: true")
        log = EventLog(instance.manifest.state_dir)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), FailingRunner())
        await fail_routine(dispatcher, 1)
        await dispatcher.scheduler.tick()

        assert not any(isinstance(event.payload, NotificationRaised) for event in log.all_events())

    asyncio.run(scenario())


def test_a_turn_parked_on_an_approval_raises_one_notification_naming_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        _write_store_tool(instance)
        permissions(instance, 'mode = "ask"\n')
        content = "line one\n" + "x" * 200
        arguments = {"path": "notes.md", "content": content}
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": "store", "args": arguments, "id": "store-1", "type": "tool_call"}
                    ],
                )
            ]
        )
        async with recording_hub(monkeypatch) as hub:
            runner = LangGraphRunner(instance, model_factory=lambda _: model)
            result, payloads = await _run_turn(runner)

        assert isinstance(result, ParkedTurn)
        approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
        [command] = hub.raised
        assert (command.source, command.title, command.options) == (
            "Untitled thread",
            "Approval waiting",
            ["Approve", "Deny"],
        )
        assert command.text == f"store: path=notes.md, content=line one {'x' * 70}…"
        assert command.approval is not None
        assert command.approval.request == approval
        assert isinstance(payloads[-1], NotificationAccepted)

    asyncio.run(scenario())
