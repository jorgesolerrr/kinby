"""The browser tools against a fake browser service: a turn opens a page under the seen-URL rule,
reads it, acts on it under the gate, and gets a context of its own (ADR 0113)."""

import asyncio
import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    ApprovalRequested,
    Event,
    GateOutcome,
    Payload,
    RoutineGrant,
    RoutineName,
    SystemPrompt,
    ToolGated,
    ToolResult,
    is_turn_closing,
)
from kinby.core import LangGraphRunner
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
from tests.test_plugins import ScriptedModel
from tests.test_routine_grants import gates, run_routine
from tests.test_routines import routine_file, signal_runtime

_JOBS = "https://jobs.example.com/applications"
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
    ) -> None:
        self.opened: list[UUID] = []
        self.closed: list[UUID] = []
        self.visited: list[str] = []
        self.snapshots: list[tuple[bool, int]] = []
        self.acted: list[BrowserAction] = []
        self._refusal = refusal
        self._snapshot = snapshot
        self._downloads = downloads
        self._url = "about:blank"

    async def open(self, turn: UUID) -> None:
        if self._refusal is not None:
            raise ConnectionError(self._refusal)
        if turn not in self.opened:
            self.opened.append(turn)

    async def run(self, turn: UUID, action: BrowserAction) -> Page:
        assert turn in self.opened
        match action:
            case Navigate(url=url):
                self.visited.append(url)
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
    } <= names


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
