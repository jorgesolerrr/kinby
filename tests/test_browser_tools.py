"""The browser tools against a fake browser service: a turn opens a page under the seen-URL rule,
reads it, acts on it under the gate, and gets a context of its own (ADR 0113)."""

import asyncio
import base64
import socket
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import aiohttp
import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    ApprovalRequested,
    BrowserProfile,
    BrowserProfileListResult,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Event,
    FrameType,
    GateOutcome,
    NotificationRaised,
    Payload,
    RoutineGrant,
    RoutineName,
    Scope,
    Stream,
    SystemPrompt,
    TakeoverFrame,
    TakeoverInput,
    TakeoverKey,
    TakeoverPointer,
    TakeoverRequested,
    ThreadStatus,
    ToolGated,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    is_turn_closing,
)
from kinby.core import LangGraphRunner
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import PreparedTurnRequest, TurnContext
from kinby.instance import Instance, load_instance
from kinby.instance.routine_grants import RoutineGrants
from kinby.plugins.browser import (
    BrowserAction,
    BrowserService,
    Click,
    Download,
    Navigate,
    Page,
    PageRead,
    Press,
    Scroll,
    Select,
    Type,
    Upload,
    Wait,
)
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE
from tests.helpers import thread_events
from tests.test_hub import FakeRuntime
from tests.test_hub_routing import control_token, instance_served, running_instance
from tests.test_hub_server import call as call_socket
from tests.test_hub_server import frame, hub_at, served, url
from tests.test_plugins import ScriptedModel
from tests.test_routine_grants import gates, run_routine, turn_events
from tests.test_routines import routine_file, signal_runtime
from tests.test_scheduler import call

_JOBS = "https://jobs.example.com/applications"
_FRAME = b"\xff\xd8 a frame of the page"
_SNAPSHOT = '- paragraph [ref=e3]: You have 3 open applications.\n- link "Next" [ref=e5]'


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch) -> None:
    """Resolve every name to a public address, and IP literals to themselves."""

    def getaddrinfo(host, port, *args, **kwargs):
        address = host if host[0].isdigit() else "93.184.215.14"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


class FakeBrowser:
    """A browser service with one page, which records each turn's context and gives its
    downloads to the first action."""

    def __init__(
        self,
        *,
        refusal: str | None = None,
        snapshot: str = _SNAPSHOT,
        downloads: tuple[Download, ...] = (),
        stall_navigation: bool = False,
    ) -> None:
        self.opened: list[UUID] = []
        self.closed: list[UUID] = []
        self.visited: list[str] = []
        self.snapshots: list[tuple[bool, int]] = []
        self.acted: list[BrowserAction] = []
        self.sent: list[TakeoverInput] = []
        #: The profile each turn's context opened with.
        self.signed_in: dict[UUID, str | None] = {}
        self.saved: list[tuple[UUID, str]] = []
        self.kept: list[BrowserProfile] = []
        self._refusal = refusal
        self._snapshot = snapshot
        self._downloads = downloads
        self._stall_navigation = stall_navigation
        self.navigating = asyncio.Event()
        self._url = "about:blank"

    async def open(self, turn: UUID, profile: str | None = None) -> None:
        if self._refusal is not None:
            raise ConnectionError(self._refusal)
        if turn not in self.opened:
            self.opened.append(turn)
            self.signed_in[turn] = profile

    async def profiles(self) -> BrowserProfileListResult:
        return BrowserProfileListResult(profiles=tuple(self.kept))

    async def save_profile(self, turn: UUID, name: str) -> BrowserProfile:
        assert turn in self.opened
        self.saved.append((turn, name))
        profile = BrowserProfile(name=name, site=urlsplit(self._url).hostname or self._url)
        self.kept.append(profile)
        return profile

    async def run(self, turn: UUID, action: BrowserAction) -> Page:
        assert turn in self.opened
        match action:
            case Navigate(url=url):
                self.visited.append(url)
                self.navigating.set()
                if self._stall_navigation:
                    await asyncio.Event().wait()
                self._url = url
            case Scroll() | Wait():
                pass
            case _:
                self.acted.append(action)
        downloads, self._downloads = self._downloads, ()
        return Page(url=self._url, title="Applications", downloads=downloads)

    async def snapshot(self, turn: UUID, *, interactive: bool, max_chars: int) -> PageRead:
        assert turn in self.opened
        self.snapshots.append((interactive, max_chars))
        return PageRead(url=self._url, text=self._snapshot)

    async def text(self, turn: UUID, ref: str | None) -> PageRead:
        assert turn in self.opened
        return PageRead(url=self._url, text="You have 3 open applications." if ref else "All")

    async def screenshot(self, turn: UUID) -> bytes:
        return b"\x89PNG"

    async def screencast(self, turn: UUID) -> AsyncGenerator[bytes]:
        assert turn in self.opened
        yield _FRAME
        await asyncio.Event().wait()

    async def send(self, turn: UUID, user_input: TakeoverInput) -> None:
        assert turn in self.opened
        self.sent.append(user_input)

    async def close(self, turn: UUID) -> None:
        self.closed.append(turn)


def _instance(tmp_path: Path, *, browser: bool = True, mode: str = "ask") -> Instance:
    path = tmp_path / "instance"
    path.mkdir()
    (path / "workspace").mkdir()
    (path / "kinby.toml").write_text(
        f'id = "test"\nbrowser = {str(browser).lower()}\n\n'
        '[models]\nmain = "openai:gpt-5"\nrecap = "custom:unpriced"\n',
        encoding="utf-8",
    )
    (path / "permissions.toml").write_text(f'mode = "{mode}"\n', encoding="utf-8")
    return load_instance(path)


def _calls(*steps: tuple[str, dict[str, object]]) -> ScriptedModel:
    """A model that makes one tool call per step, then finishes."""
    return ScriptedModel(
        [
            *(
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": name, "args": args, "id": f"call-{index}", "type": "tool_call"}
                    ],
                )
                for index, (name, args) in enumerate(steps)
            ),
            AIMessageChunk(content="Done."),
        ]
    )


def _turn(
    instance: Instance, model: ScriptedModel, message: str, browser: BrowserService
) -> tuple[UUID, list[Payload]]:
    runner = LangGraphRunner(instance, model_factory=lambda _: model, browser=browser)
    preparation = runner.prepare_for_turn()
    thread_id, turn_id = uuid4(), uuid4()
    payloads: list[Payload] = []

    async def emit(payload: Payload) -> Event:
        payloads.append(payload)
        return Event(
            sequence=len(payloads),
            thread_id=thread_id,
            turn_id=turn_id,
            payload=payload,
            timestamp=datetime.now(UTC),
        )

    asyncio.run(
        runner.run(
            PreparedTurnRequest(
                thread_id=thread_id,
                turn_id=turn_id,
                message=message,
                model=preparation.model,
                permission_mode=preparation.default_mode,
                system_prompt=SystemPrompt("System prompt"),
            ),
            TurnContext(preparation.budgets, emit),
        )
    )
    return turn_id, payloads


def _results(payloads: list[Payload]) -> dict[str, ToolResult]:
    return {p.name: p for p in payloads if isinstance(p, ToolResult)}


def test_a_turn_opens_a_url_from_the_users_message_without_asking_and_reads_the_page(
    tmp_path,
) -> None:
    browser = FakeBrowser()
    model = _calls(
        ("browser_open", {"url": _JOBS}),
        ("browser_snapshot", {}),
        ("browser_text", {"ref": "e3"}),
    )

    turn, payloads = _turn(_instance(tmp_path), model, f"Check {_JOBS}", browser)

    gates = [p for p in payloads if isinstance(p, ToolGated)]
    assert [(g.name, g.action, g.rule) for g in gates] == [
        ("browser_open", GateOutcome.ALLOW, "web.seen_url"),
        ("browser_snapshot", GateOutcome.ALLOW, "mode.ask.read"),
        ("browser_text", GateOutcome.ALLOW, "mode.ask.read"),
    ]
    results = _results(payloads)
    assert not any(result.error for result in results.values())
    assert browser.visited == [_JOBS]
    assert browser.snapshots == [(True, 20_000)]
    snapshot = results["browser_snapshot"].output
    assert snapshot.startswith(f'<untrusted-content source="{_JOBS}">')
    assert "[ref=e3]" in snapshot
    text = results["browser_text"].output
    assert text.startswith("<untrusted-content")
    assert "You have 3 open applications." in text
    assert browser.opened == [turn]
    assert browser.closed == [turn]


def test_browser_open_on_a_url_the_model_built_asks_in_ask_mode(tmp_path) -> None:
    browser = FakeBrowser()
    model = _calls(("browser_open", {"url": f"{_JOBS}?token=secret"}))

    _, payloads = _turn(_instance(tmp_path), model, "Check my applications.", browser)

    approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
    assert (approval.name, approval.rule) == ("browser_open", "web.unseen_url")
    assert browser.visited == []


@pytest.mark.parametrize("url", ["http://10.0.0.1/admin", "http://127.0.0.1:8080/"])
def test_browser_open_refuses_a_private_address(tmp_path, url: str) -> None:
    browser = FakeBrowser()
    model = _calls(("browser_open", {"url": url}))

    _, payloads = _turn(_instance(tmp_path), model, f"Open {url}", browser)

    result = _results(payloads)["browser_open"]
    assert result.error
    assert "not a public address" in result.output
    assert browser.visited == []


def test_a_later_snapshot_page_link_is_seen_by_browser_open(tmp_path) -> None:
    next_page = "https://jobs.example.com/applications?page=2"
    browser = FakeBrowser(snapshot=f'- link "Next" [ref=e5]:\n  - /url: {next_page}')
    model = _calls(
        ("browser_open", {"url": _JOBS}),
        ("browser_snapshot", {}),
        ("browser_open", {"url": next_page}),
    )

    _, payloads = _turn(_instance(tmp_path), model, f"Check {_JOBS}", browser)

    opens = [p for p in payloads if isinstance(p, ToolGated) and p.name == "browser_open"]
    assert [g.rule for g in opens] == ["web.seen_url", "web.seen_url"]
    assert browser.visited == [_JOBS, next_page]


def test_a_browser_off_instance_has_no_browser_tools(tmp_path) -> None:
    browser = FakeBrowser()
    model = ScriptedModel([AIMessageChunk(content="Done.")])

    _turn(_instance(tmp_path, browser=False), model, "Hello", browser)

    names = {tool.name for tool in model.bound_tools[0]}
    assert not any(name.startswith("browser_") for name in names)
    assert browser.opened == []


def test_a_browser_on_instance_has_the_browser_tools(tmp_path) -> None:
    model = ScriptedModel([AIMessageChunk(content="Done.")])

    _turn(_instance(tmp_path), model, "Hello", FakeBrowser())

    names = {tool.name for tool in model.bound_tools[0]}
    assert {
        "browser_open",
        "browser_snapshot",
        "browser_text",
        "browser_screenshot",
        "browser_scroll",
        "browser_wait",
        "browser_click",
        "browser_type",
        "browser_select",
        "browser_press",
        "browser_upload",
        "browser_profiles",
    } <= names


def test_browser_open_with_a_profile_opens_the_turns_browser_signed_in_with_it(tmp_path) -> None:
    browser = FakeBrowser()
    model = _calls(("browser_open", {"url": _JOBS, "profile": "LinkedIn"}))

    turn, payloads = _turn(_instance(tmp_path), model, f"Check {_JOBS}", browser)

    assert not _results(payloads)["browser_open"].error
    assert browser.signed_in == {turn: "LinkedIn"}
    assert browser.visited == [_JOBS]


def test_browser_profiles_lists_the_instances_profiles_by_name_and_site(tmp_path) -> None:
    browser = FakeBrowser()
    browser.kept = [
        BrowserProfile(name="Indeed", site="secure.indeed.com"),
        BrowserProfile(name="LinkedIn", site="www.linkedin.com"),
    ]
    model = _calls(("browser_profiles", {}))

    _, payloads = _turn(_instance(tmp_path), model, "Which sites can you use?", browser)

    [gate] = [p for p in payloads if isinstance(p, ToolGated)]
    assert gate.action is GateOutcome.ALLOW
    assert _results(payloads)["browser_profiles"].output == (
        "Indeed: secure.indeed.com\nLinkedIn: www.linkedin.com"
    )
    assert browser.opened == []


def test_browser_profiles_says_when_there_is_none(tmp_path) -> None:
    model = _calls(("browser_profiles", {}))

    _, payloads = _turn(_instance(tmp_path), model, "Which sites?", FakeBrowser())

    assert _results(payloads)["browser_profiles"].output == (
        "You have no browser profiles. The user creates them from the instance page."
    )


def test_a_turn_without_a_browser_call_opens_no_context(tmp_path) -> None:
    browser = FakeBrowser()

    _turn(_instance(tmp_path), ScriptedModel([AIMessageChunk(content="Hi.")]), "Hi", browser)

    assert browser.opened == []


def test_two_turns_get_two_contexts(tmp_path) -> None:
    browser = FakeBrowser()
    instance = _instance(tmp_path)

    first, _ = _turn(instance, _calls(("browser_snapshot", {})), "Look", browser)
    second, _ = _turn(instance, _calls(("browser_snapshot", {})), "Look", browser)

    assert browser.opened == [first, second]
    assert browser.closed == [first, second]


def test_a_screenshot_lands_in_the_workspace(tmp_path) -> None:
    instance = _instance(tmp_path)
    model = _calls(("browser_screenshot", {}))

    _, payloads = _turn(instance, model, "Look", FakeBrowser())

    result = _results(payloads)["browser_screenshot"]
    assert not result.error, result.output
    saved = list((instance.manifest.workspace.path / "screenshots").glob("*.png"))
    assert [path.read_bytes() for path in saved] == [b"\x89PNG"]
    assert f"screenshots/{saved[0].name}" in result.output


def test_a_browser_the_service_cannot_give_reaches_the_model_as_a_tool_error(tmp_path) -> None:
    refusal = "The hub refused the browser: all 3 browsers are in use."
    model = _calls(("browser_snapshot", {}))

    _, payloads = _turn(_instance(tmp_path), model, "Look", FakeBrowser(refusal=refusal))

    result = _results(payloads)["browser_snapshot"]
    assert result.error
    assert result.output == f"ConnectionError: {refusal}"


#: Each action tool, its arguments, and the action it runs, given the workspace.
_ACTIONS = [
    ("browser_click", {"ref": "e5"}, lambda _: Click("e5")),
    ("browser_type", {"ref": "e7", "text": "Ada"}, lambda _: Type("e7", "Ada")),
    ("browser_select", {"ref": "e8", "value": "pm"}, lambda _: Select("e8", "pm")),
    ("browser_press", {"key": "Enter"}, lambda _: Press("Enter")),
    (
        "browser_upload",
        {"ref": "e9", "path": "cv.pdf"},
        lambda workspace: Upload("e9", (workspace / "cv.pdf").resolve()),
    ),
]


@pytest.mark.parametrize(("name", "args", "action"), _ACTIONS)
def test_an_action_asks_in_ask_mode(tmp_path, name, args, action) -> None:
    browser = FakeBrowser()

    _, payloads = _turn(_instance(tmp_path), _calls((name, args)), "Apply.", browser)

    approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
    assert (approval.name, approval.rule) == (name, "mode.ask.write")
    assert browser.acted == []


@pytest.mark.parametrize(("name", "args", "action"), _ACTIONS)
def test_an_action_is_denied_in_read_only(tmp_path, name, args, action) -> None:
    browser = FakeBrowser()
    instance = _instance(tmp_path, mode="read-only")

    _, payloads = _turn(instance, _calls((name, args)), "Apply.", browser)

    [gate] = [p for p in payloads if isinstance(p, ToolGated)]
    assert (gate.action, gate.rule) == (GateOutcome.DENY, "mode.read-only.write")
    assert browser.acted == []


@pytest.mark.parametrize(("name", "args", "action"), _ACTIONS)
def test_an_action_runs_on_a_routine_turn_with_a_grant(tmp_path, name, args, action) -> None:
    browser = FakeBrowser()
    instance = _instance(tmp_path)
    (instance.manifest.workspace.path / "cv.pdf").write_bytes(b"%PDF")
    routine_file(instance, "description: Apply", "Apply to the open jobs.")
    instance.manifest.state_dir.mkdir(exist_ok=True)
    RoutineGrants(instance.manifest.state_dir).grant(
        RoutineGrant(
            routine=RoutineName("news"),
            tool=name,
            granted_at=datetime.now(UTC),
            approval_id=uuid4(),
        )
    )
    dispatcher, _ = signal_runtime(instance, _calls((name, args)), browser=browser)

    async def scenario() -> list[ToolGated]:
        stop = await run_routine(dispatcher)
        assert is_turn_closing(stop.payload)
        return await gates(dispatcher, stop.thread_id)

    decided = asyncio.run(scenario())

    assert [(gate.name, gate.action, gate.rule) for gate in decided] == [
        (name, GateOutcome.ALLOW, "routine.grant")
    ]
    assert browser.acted == [action(instance.manifest.workspace.path)]


def test_a_download_lands_in_the_workspace_where_the_file_tools_read_it(tmp_path) -> None:
    report = Download("report.csv", b"company,status\nAcme,open\n")
    browser = FakeBrowser(downloads=(report,))
    model = _calls(("browser_click", {"ref": "e5"}), ("read", {"path": "downloads/report.csv"}))

    _, payloads = _turn(_instance(tmp_path, mode="full-access"), model, "Get it.", browser)

    results = _results(payloads)
    assert "downloads/report.csv" in results["browser_click"].output
    assert results["read"].output == "company,status\nAcme,open\n"


def test_a_download_that_begins_late_lands_with_the_next_browser_wait(tmp_path) -> None:
    report = Download("report.csv", b"company,status\nAcme,open\n")
    browser = FakeBrowser(downloads=(report,))
    instance = _instance(tmp_path)

    _, payloads = _turn(instance, _calls(("browser_wait", {})), "Wait for it.", browser)

    assert "Downloaded downloads/report.csv." in _results(payloads)["browser_wait"].output
    downloads = instance.manifest.workspace.path / "downloads"
    assert (downloads / "report.csv").read_bytes() == report.content


def test_a_download_keeps_inside_downloads_whatever_the_page_names_it(tmp_path) -> None:
    browser = FakeBrowser(downloads=(Download("../../kinby.toml", b"id = 'evil'"),))
    instance = _instance(tmp_path, mode="full-access")
    kinby_toml = (instance.path / "kinby.toml").read_text(encoding="utf-8")

    _turn(instance, _calls(("browser_click", {"ref": "e5"})), "Get it.", browser)

    assert (instance.path / "kinby.toml").read_text(encoding="utf-8") == kinby_toml
    downloads = instance.manifest.workspace.path / "downloads"
    assert (downloads / "kinby.toml").read_bytes() == b"id = 'evil'"


@pytest.mark.parametrize("path", ["../kinby.toml", "/etc/passwd", "env"])
def test_an_upload_from_outside_the_workspace_is_refused(tmp_path, path: str) -> None:
    browser = FakeBrowser()
    instance = _instance(tmp_path, mode="full-access")
    (instance.path / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    (instance.manifest.workspace.path / "env").symlink_to(instance.path / ".env")
    model = _calls(("browser_upload", {"ref": "e9", "path": path}))

    _, payloads = _turn(instance, model, "Attach it.", browser)

    result = _results(payloads)["browser_upload"]
    assert result.error
    assert "outside the workspace" in result.output
    assert browser.acted == []


_REASON = "Sign in to LinkedIn: it asks for a code from your phone."


def _takeover_runtime(
    tmp_path: Path, browser: FakeBrowser, *after: tuple[str, dict[str, object]]
) -> Dispatcher:
    """An instance whose turns call browser_takeover once, then the tools *after*, then
    finish."""
    instance = _instance(tmp_path, mode="full-access")
    log = EventLog(instance.manifest.state_dir)
    model = _calls(("browser_takeover", {"reason": _REASON}), *after)
    runner = LangGraphRunner(
        instance, event_log=log, model_factory=lambda _: model, browser=browser
    )
    return build_dispatcher(
        instance.manifest.state_dir,
        event_log=log,
        turns=TurnConfig(
            runner.prepare_for_turn, runner.permission_ceiling, runner, browser=browser
        ),
    )


async def _parked_on_takeover(dispatcher: Dispatcher) -> Event:
    """Start a turn, and return the takeover it parks on."""
    created = await call(dispatcher, "thread.create")
    await call(dispatcher, "thread.turn.start", thread_id=created.id, message="Check LinkedIn")
    items = await thread_events(dispatcher, {"thread_id": created.id})
    async with asyncio.timeout(5):
        async for item in items:
            assert isinstance(item, Event)
            assert not is_turn_closing(item.payload), item
            if isinstance(item.payload, TakeoverRequested):
                await items.aclose()
                return item
    raise AssertionError("The thread stopped streaming.")


async def _finish(dispatcher: Dispatcher, parked: Event, profile: str | None = None) -> object:
    assert isinstance(parked.payload, TakeoverRequested)
    return await call(
        dispatcher,
        "thread.takeover.finish",
        thread_id=parked.thread_id,
        takeover_id=parked.payload.takeover_id,
        profile=profile,
    )


def test_a_takeover_parks_the_turn_needing_the_user_and_finishing_resumes_it(tmp_path) -> None:
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)

    async def scenario() -> tuple[Event, list[ThreadStatus], list[UUID], list[Event]]:
        parked = await _parked_on_takeover(dispatcher)
        listed = await call(dispatcher, "thread.list")
        closed_while_parked = list(browser.closed)
        await _finish(dispatcher, parked)
        events = await turn_events(dispatcher, parked.thread_id)
        return parked, [thread.status for thread in listed.threads], closed_while_parked, events

    parked, statuses, closed_while_parked, events = asyncio.run(scenario())

    assert isinstance(parked.payload, TakeoverRequested)
    assert parked.payload.reason == _REASON
    assert statuses == [ThreadStatus.AWAITING_TAKEOVER]
    assert closed_while_parked == []
    [result] = [e.payload for e in events if isinstance(e.payload, ToolResult)]
    assert (result.name, result.error) == ("browser_takeover", False)
    assert isinstance(events[-1].payload, TurnCompleted)
    assert browser.closed == [parked.turn_id]


def test_a_takeover_finished_as_a_profile_is_in_the_agents_list_by_name_and_site(
    tmp_path,
) -> None:
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser, ("browser_profiles", {}))

    async def scenario() -> tuple[Event, list[Event]]:
        parked = await _parked_on_takeover(dispatcher)
        # The user's live view opened the browser, and they signed in.
        await browser.open(parked.turn_id)
        await browser.run(parked.turn_id, Navigate("https://www.linkedin.com/feed/"))
        await _finish(dispatcher, parked, "LinkedIn")
        return parked, await turn_events(dispatcher, parked.thread_id)

    parked, events = asyncio.run(scenario())

    assert browser.saved == [(parked.turn_id, "LinkedIn")]
    results = {e.payload.name: e.payload for e in events if isinstance(e.payload, ToolResult)}
    assert results["browser_profiles"].output == "LinkedIn: www.linkedin.com"
    assert isinstance(events[-1].payload, TurnCompleted)


def test_a_takeover_raises_a_notification_with_its_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(HUB_URL_VARIABLE, "ws://127.0.0.1:9/hub")
    monkeypatch.setenv(HUB_SCOPES_VARIABLE, Scope.NOTIFICATION_RAISE.value)
    browser = FakeBrowser()
    model = _calls(("browser_takeover", {"reason": _REASON}))

    _, payloads = _turn(_instance(tmp_path, mode="full-access"), model, "Check", browser)

    [raised] = [p for p in payloads if isinstance(p, NotificationRaised)]
    assert (raised.title, raised.text, raised.options) == ("Takeover waiting", _REASON, [])
    assert browser.closed == []


def test_in_ask_mode_the_takeover_asks_the_user_in_place_of_an_approval(tmp_path) -> None:
    browser = FakeBrowser()
    model = _calls(("browser_open", {"url": _JOBS}), ("browser_takeover", {"reason": _REASON}))

    turn, payloads = _turn(_instance(tmp_path), model, f"Check {_JOBS}", browser)

    assert not any(isinstance(p, ApprovalRequested) for p in payloads)
    assert any(isinstance(p, TakeoverRequested) for p in payloads)
    assert browser.opened == [turn]
    assert browser.closed == []


def test_in_read_only_the_takeover_is_denied(tmp_path) -> None:
    model = _calls(("browser_takeover", {"reason": _REASON}))

    _, payloads = _turn(_instance(tmp_path, mode="read-only"), model, "Check", FakeBrowser())

    assert not any(isinstance(p, TakeoverRequested) for p in payloads)
    [gate] = [p for p in payloads if isinstance(p, ToolGated)]
    assert (gate.action, gate.rule) == (GateOutcome.DENY, "mode.read-only.write")


def test_the_live_view_streams_the_page_and_passes_the_users_input(tmp_path) -> None:
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)
    sent: list[TakeoverInput] = [
        TakeoverPointer(action="down", x=120, y=48),
        TakeoverPointer(action="up", x=120, y=48),
        TakeoverKey(key="Enter"),
    ]

    async def scenario() -> ContractModel:
        parked = await _parked_on_takeover(dispatcher)
        assert isinstance(parked.payload, TakeoverRequested)
        takeover = {"thread_id": parked.thread_id, "takeover_id": parked.payload.takeover_id}
        stream = await dispatcher.subscribe("thread.takeover.start", takeover, set(Scope))
        assert not isinstance(stream, ErrorEnvelope), stream
        async with asyncio.timeout(5):
            frame = await anext(stream.items)
        await stream.aclose()
        for user_input in sent:
            await call(
                dispatcher,
                "thread.takeover.input",
                **takeover,
                input=user_input.model_dump(mode="json"),
            )
        return frame

    frame = asyncio.run(scenario())

    assert isinstance(frame, TakeoverFrame)
    assert base64.b64decode(frame.data) == _FRAME
    assert browser.sent == sent


def test_a_takeover_another_turn_parked_on_is_not_found(tmp_path) -> None:
    dispatcher = _takeover_runtime(tmp_path, FakeBrowser())

    async def scenario() -> object:
        parked = await _parked_on_takeover(dispatcher)
        return await call(
            dispatcher, "thread.takeover.finish", thread_id=parked.thread_id, takeover_id=uuid4()
        )

    refused = asyncio.run(scenario())

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.NOT_FOUND


def test_an_abandoned_takeover_ends_the_turn_and_closes_its_browser(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("kinby.core.turns.TAKEOVER_SECONDS", 0.1)
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)

    async def scenario() -> tuple[Event, list[Event], object]:
        parked = await _parked_on_takeover(dispatcher)
        events = await turn_events(dispatcher, parked.thread_id)
        return parked, events, await _finish(dispatcher, parked)

    parked, events, late_finish = asyncio.run(scenario())

    closing = events[-1].payload
    assert isinstance(closing, TurnFailed)
    assert "takeover" in closing.message
    assert browser.closed == [parked.turn_id]
    assert isinstance(late_finish, ErrorEnvelope)


def test_stopping_a_turn_parked_on_a_takeover_closes_its_browser(tmp_path) -> None:
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)

    async def scenario() -> tuple[Event, list[Event]]:
        parked = await _parked_on_takeover(dispatcher)
        await call(dispatcher, "thread.turn.interrupt", thread_id=parked.thread_id)
        return parked, await turn_events(dispatcher, parked.thread_id)

    parked, events = asyncio.run(scenario())

    assert isinstance(events[-1].payload, TurnInterrupted)
    assert browser.closed == [parked.turn_id]


def test_stopping_a_takeover_while_its_notification_is_out_closes_its_browser(
    tmp_path, monkeypatch
) -> None:
    async def never_answered(raised: NotificationRaised, report: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("kinby.core.turn_runner.raises_notifications", lambda: True)
    monkeypatch.setattr("kinby.core.turn_runner.raise_notification", never_answered)
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)

    async def scenario() -> tuple[Event, list[Event]]:
        parked = await _parked_on_takeover(dispatcher)
        await call(dispatcher, "thread.turn.interrupt", thread_id=parked.thread_id)
        return parked, await turn_events(dispatcher, parked.thread_id)

    parked, events = asyncio.run(scenario())

    assert isinstance(events[-1].payload, TurnInterrupted)
    assert browser.closed == [parked.turn_id]


def test_through_the_hub_input_reaches_the_browser_and_finishing_resumes_the_turn(
    tmp_path,
) -> None:
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)

    async def scenario() -> tuple[list[dict[str, object]], list[Event]]:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            instance_id = await running_instance(hub, session, address)
            async with instance_served(dispatcher, control_token(hub, instance_id)) as private:
                runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                parked = await _parked_on_takeover(dispatcher)
                assert isinstance(parked.payload, TakeoverRequested)
                takeover = {
                    "thread_id": str(parked.thread_id),
                    "takeover_id": str(parked.payload.takeover_id),
                }
                async with session.ws_connect(
                    url(address, f"/instances/{instance_id}/ws")
                ) as relayed:
                    await relayed.send_json(
                        {
                            "type": "subscribe",
                            "id": "view",
                            "method": "thread.takeover.start",
                            "params": takeover,
                        }
                    )
                    received = [await frame(relayed), await frame(relayed)]
                    await call_socket(
                        relayed,
                        "thread.takeover.input",
                        **takeover,
                        input={"type": "key", "key": "Enter"},
                    )
                    received.append(await frame(relayed))
                    await call_socket(relayed, "thread.takeover.finish", **takeover)
                    received.append(await frame(relayed))
                events = await turn_events(dispatcher, parked.thread_id)
        return received, events

    received, events = asyncio.run(scenario())

    subscribed, item, input_result, finish_result = received
    assert subscribed["type"] == FrameType.SUBSCRIBED.value
    assert isinstance(item["item"], dict)
    assert base64.b64decode(str(item["item"]["data"])) == _FRAME
    assert input_result["type"] == FrameType.RESULT.value, input_result
    assert finish_result["type"] == FrameType.RESULT.value, finish_result
    assert browser.sent == [TakeoverKey(key="Enter")]
    assert isinstance(events[-1].payload, TurnCompleted)


_SIGN_IN = "https://www.linkedin.com/login"


async def _watch_takeover(dispatcher: Dispatcher, takeover_id: UUID) -> Stream[ContractModel]:
    """Start a takeover with no turn, at the sign-in page, and watch it."""
    stream = await dispatcher.subscribe(
        "browser.takeover.start", {"takeover_id": takeover_id, "url": _SIGN_IN}, set(Scope)
    )
    assert not isinstance(stream, ErrorEnvelope), stream
    return stream


def test_a_takeover_with_no_turn_opens_the_page_takes_input_and_saves_a_profile(tmp_path) -> None:
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)
    takeover_id = uuid4()

    async def scenario() -> tuple[ContractModel, object, list[UUID]]:
        stream = await _watch_takeover(dispatcher, takeover_id)
        async with asyncio.timeout(5):
            frame = await anext(stream.items)
        await call(
            dispatcher,
            "browser.takeover.input",
            takeover_id=takeover_id,
            input={"type": "key", "key": "Enter"},
        )
        saved = await call(
            dispatcher, "browser.takeover.finish", takeover_id=takeover_id, profile="LinkedIn"
        )
        closed_on_finish = list(browser.closed)
        await stream.aclose()
        return frame, saved, closed_on_finish

    frame, saved, closed_on_finish = asyncio.run(scenario())

    assert isinstance(frame, TakeoverFrame)
    assert base64.b64decode(frame.data) == _FRAME
    assert browser.visited == [_SIGN_IN]
    assert browser.sent == [TakeoverKey(key="Enter")]
    assert saved == BrowserProfile(name="LinkedIn", site="www.linkedin.com")
    assert browser.saved == [(takeover_id, "LinkedIn")]
    assert closed_on_finish == [takeover_id]


def test_a_takeover_with_no_turn_closes_its_browser_when_nobody_watches_it(tmp_path) -> None:
    browser = FakeBrowser()
    dispatcher = _takeover_runtime(tmp_path, browser)
    takeover_id = uuid4()

    async def scenario() -> object:
        stream = await _watch_takeover(dispatcher, takeover_id)
        async with asyncio.timeout(5):
            await anext(stream.items)
        await stream.aclose()
        return await call(
            dispatcher, "browser.takeover.finish", takeover_id=takeover_id, profile="LinkedIn"
        )

    late_finish = asyncio.run(scenario())

    assert browser.closed == [takeover_id]
    assert isinstance(late_finish, ErrorEnvelope)
    assert late_finish.code is ErrorCode.NOT_FOUND
    assert browser.saved == []


def test_a_takeover_with_no_turn_left_while_its_page_loads_closes_its_browser(tmp_path) -> None:
    browser = FakeBrowser(stall_navigation=True)
    dispatcher = _takeover_runtime(tmp_path, browser)
    takeover_id = uuid4()

    async def scenario() -> None:
        watching = asyncio.create_task(_watch_takeover(dispatcher, takeover_id))
        async with asyncio.timeout(5):
            await browser.navigating.wait()
        watching.cancel()
        with suppress(asyncio.CancelledError):
            await watching

    asyncio.run(scenario())

    assert browser.closed == [takeover_id]
