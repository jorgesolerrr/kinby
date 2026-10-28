"""The browser service: its proxy refuses every address that is not public, and an instance reads
and acts on a page in Playwright's browser server through the hub's browser methods, with its
control token (ADR 0114)."""

import asyncio
import base64
import json
import re
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from uuid import UUID, uuid4

import aiohttp
import httpx
import pytest
from aiohttp import web
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from kinby.contracts import (
    BROWSER_FILE_BYTES,
    BROWSER_PROFILE_LIST,
    BROWSER_TAKEOVER_FINISH,
    BROWSER_TAKEOVER_INPUT,
    BROWSER_TAKEOVER_START,
    HUB_SCOPES,
    INSTANCE_STOP,
    THREAD_TAKEOVER_FINISH,
    THREAD_TAKEOVER_INPUT,
    THREAD_TAKEOVER_RESUME,
    THREAD_TAKEOVER_START,
    AcceptedResult,
    BrowserProfile,
    BrowserProfileListCommand,
    BrowserProfileListResult,
    BrowserTakeoverFinishCommand,
    BrowserTakeoverInputCommand,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    InstanceStopCommand,
    LifecycleOperationResult,
    OperationState,
    Stream,
    TakeoverFrame,
    TakeoverKey,
    TakeoverPointer,
    TakeoverWheel,
    ThreadTakeoverFinishCommand,
    ThreadTakeoverInputCommand,
    ThreadTakeoverInputResult,
    ThreadTakeoverResumeCommand,
    VaultEntryAddCommand,
    VaultField,
)
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.dispatcher import Dispatcher
from kinby.hub.browser import BrowserLimits
from kinby.hub.browser_service import serve_proxy
from kinby.hub.vault import totp
from kinby.plugins import hub_browser
from kinby.plugins.browser import (
    BrowserAction,
    Click,
    Download,
    FillCredential,
    Navigate,
    Page,
    Press,
    Scroll,
    Select,
    Type,
    Upload,
    Wait,
)
from kinby.plugins.defaults.web import REFUSED_HEADER, public_address
from kinby.plugins.hub import HUB_URL_VARIABLE
from kinby.plugins.hub_browser import HubBrowser
from tests.test_hub import finished_operation, hub_client
from tests.test_hub_browser import BrowserHub, browser_hub, browser_on, error, result
from tests.test_hub_routing import instance_served
from tests.test_hub_server import frame

_PAGE = """<html><head><title>Applications</title></head><body>
<h1>Applications</h1>
<p>You have 3 open applications.</p>
<nav><a href="/next">Next page</a></nav>
<form><label>Search <input name="q"></label><button>Go</button></form>
<ul>{items}</ul>
</body></html>"""
_ITEMS = "".join(f'<li><a href="/{index}">Company {index}</a></li>' for index in range(400))
_FORM = """<html><head><title>Apply</title></head><body>
<p id="said">Nothing yet.</p>
<input aria-label="Name" oninput="said.textContent = 'Name: ' + this.value"
  onkeydown="if (event.key === 'Enter') said.textContent = 'Sent ' + this.value">
<input type="password" aria-label="Password"
  onkeydown="said.textContent = 'Key: ' + event.key">
<select aria-label="Role" onchange="said.textContent = 'Role: ' + this.value">
  <option value="dev">Developer</option><option value="pm">Manager</option>
</select>
<button onclick="said.textContent = 'Applied'">Apply</button>
<div style="position: relative">
  <button onclick="said.textContent = 'Withdrawn'">Withdraw</button>
  <div style="position: absolute; inset: 0; background: white">Cookies</div>
</div>
<a href="/report.csv" download>Report</a>
<a href="/export.csv" download>Export</a>
<a href="/big.bin" download>Archive</a>
<input type="file" aria-label="CV"
  onchange="said.textContent = 'Attached ' + this.files[0].name + ' as ' + this.files[0].type">
</body></html>"""
_REPORT = b"company,status\nAcme,open\n"
#: The session cookie the site signs a browser in with.
_SESSION = "signed-in-at-the-hub"
_PAD = """<html><body style="margin: 0; height: 100vh"
  onclick="said.textContent = 'Clicked at ' + event.clientX + ',' + event.clientY">
<p id="said">Nothing yet.</p>
<script>document.onkeydown = (event) => { said.textContent += ' ' + event.key }</script>
</body></html>"""


async def _page(request: web.Request) -> web.Response:
    return web.Response(text=_PAGE.format(items=_ITEMS), content_type="text/html")


async def _form(request: web.Request) -> web.Response:
    return web.Response(text=_FORM, content_type="text/html")


#: A page in one colour, with a link that opens a page in another in a new tab.
_OPENER = """<html><body style="margin: 0; height: 100vh; background: #c00">
<a href="/blue" target="_blank">Blue</a></body></html>"""
_BLUE = """<html><body style="margin: 0; height: 100vh; background: #00c"></body></html>"""


async def _opener(request: web.Request) -> web.Response:
    return web.Response(text=_OPENER, content_type="text/html")


async def _blue(request: web.Request) -> web.Response:
    return web.Response(text=_BLUE, content_type="text/html")


async def _pad(request: web.Request) -> web.Response:
    return web.Response(text=_PAD, content_type="text/html")


async def _report(request: web.Request) -> web.Response:
    """The report, slow to answer, as a loaded server is, so its download begins after the click
    that asked for it returns."""
    await asyncio.sleep(0.2)
    return web.Response(body=_REPORT, content_type="text/csv")


async def _export(request: web.Request) -> web.Response:
    """An export, slower to answer than a click waits for its download."""
    await asyncio.sleep(1.5)
    return web.Response(body=_REPORT, content_type="text/csv")


async def _big(request: web.Request) -> web.Response:
    return web.Response(body=bytes(BROWSER_FILE_BYTES + 1), content_type="application/zip")


async def _set_cookie(request: web.Request) -> web.Response:
    response = web.Response(text="<p>Signed in.</p>", content_type="text/html")
    response.set_cookie("session", _SESSION)
    return response


async def _refresh_cookie(request: web.Request) -> web.Response:
    """The site renews the session of a browser that comes back signed in."""
    response = web.Response(text="<p>Welcome back.</p>", content_type="text/html")
    response.set_cookie("session", f"{request.cookies['session']}-renewed")
    return response


async def _cookies(request: web.Request) -> web.Response:
    sent = request.cookies.get("session", "none")
    return web.Response(text=f"<p>session: {sent}</p>", content_type="text/html")


#: A sign-in form, sent by the method the page is asked for with, such as /login?method=get.
_SIGN_IN = """<html><head><title>Sign in</title></head><body>
<form method="{method}" action="/signed-in">
<input name="username" aria-label="Email">
<input name="password" type="password" aria-label="Password">
<input name="code" aria-label="Code">
<button>Sign in</button>
</form></body></html>"""
_USERNAME = "alice@example.com"
_PASSWORD = "correct horse battery staple"
#: The seed of RFC 6238's SHA-1 test vectors.
_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


async def _sign_in(request: web.Request) -> web.Response:
    method = request.query.get("method", "post")
    return web.Response(text=_SIGN_IN.format(method=method), content_type="text/html")


async def _signed_in(request: web.Request) -> web.Response:
    """Say who signed in, and whether the password and the code were right."""
    form = request.query if request.method == "GET" else await request.post()
    password = "right" if form["password"] == _PASSWORD else "wrong"
    # The code may be of the 30 seconds before the form was sent.
    now = time.time()
    code = "right" if form["code"] in {totp(_SEED, now), totp(_SEED, now - 30)} else "wrong"
    return web.Response(
        text=f"<p>Signed in as {form['username']}. Password {password}. Code {code}.</p>",
        content_type="text/html",
    )


@asynccontextmanager
async def site(stalled: asyncio.Event | None = None) -> AsyncIterator[int]:
    """The test pages, on a local port. The proxy lets the browser reach this port alone. A page
    asked for at /stall sets *stalled*, and never loads."""
    released = asyncio.Event()

    async def stall(request: web.Request) -> web.Response:
        assert stalled is not None
        stalled.set()
        await released.wait()
        return web.Response(text="<p>Too late.</p>", content_type="text/html")

    application = web.Application()
    application.router.add_get("/stall", stall)
    application.router.add_get("/", _page)
    application.router.add_get("/form", _form)
    application.router.add_get("/pad", _pad)
    application.router.add_get("/opener", _opener)
    application.router.add_get("/blue", _blue)
    application.router.add_get("/report.csv", _report)
    application.router.add_get("/export.csv", _export)
    application.router.add_get("/big.bin", _big)
    application.router.add_get("/sign-in", _set_cookie)
    application.router.add_get("/refresh", _refresh_cookie)
    application.router.add_get("/cookies", _cookies)
    application.router.add_get("/login", _sign_in)
    application.router.add_route("*", "/signed-in", _signed_in)
    runner = web.AppRunner(application)
    await runner.setup()
    served = web.TCPSite(runner, "127.0.0.1", 0)
    await served.start()
    try:
        yield served.port
    finally:
        released.set()
        await runner.cleanup()


def _resolving_the_site(port: int):
    """Resolve the site's host as public, and every other host as the service would."""

    async def resolve(host: str, at: int) -> str:
        if (host, at) == ("site.test", port):
            return "127.0.0.1"
        return await public_address(host, at)

    return resolve


@asynccontextmanager
async def proxy(port: int) -> AsyncIterator[str]:
    server = await serve_proxy("127.0.0.1", 0, resolve=_resolving_the_site(port))
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()


def test_the_proxy_carries_a_request_to_a_public_address() -> None:
    async def scenario() -> httpx.Response:
        async with (
            site() as port,
            proxy(port) as address,
            httpx.AsyncClient(proxy=address) as client,
        ):
            return await client.get(f"http://site.test:{port}/cookies")

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert "session: none" in response.text


@pytest.mark.parametrize(
    "url", ["http://10.255.255.1/", "http://127.0.0.1:8080/", "http://169.254.169.254/latest"]
)
def test_the_proxy_refuses_an_address_that_is_not_public(url: str) -> None:
    async def scenario() -> httpx.Response:
        async with (
            site() as port,
            proxy(port) as address,
            httpx.AsyncClient(proxy=address) as client,
        ):
            return await client.get(url)

    response = asyncio.run(scenario())

    assert response.status_code == 403
    assert "not a public address" in response.headers[REFUSED_HEADER]


def test_the_proxy_refuses_a_tunnel_to_an_address_that_is_not_public() -> None:
    async def scenario() -> bytes:
        async with site() as port, proxy(port) as address:
            host, _, proxy_port = address.removeprefix("http://").partition(":")
            reader, writer = await asyncio.open_connection(host, int(proxy_port))
            writer.write(b"CONNECT 10.0.0.1:443 HTTP/1.1\r\nHost: 10.0.0.1:443\r\n\r\n")
            await writer.drain()
            answer = await reader.read()
            writer.close()
            return answer

    answer = asyncio.run(scenario())

    assert answer.startswith(b"HTTP/1.1 403")


@pytest.fixture(scope="module")
def browser_server() -> Iterator[str]:
    """Playwright's browser server, where the headless shell is installed."""
    with sync_playwright() as playwright:
        try:
            playwright.chromium.launch().close()
        except PlaywrightError as error:
            pytest.skip(f"Playwright's headless shell is not installed: {error.message}")
    server = subprocess.Popen(
        ["playwright", "run-server", "--host", "127.0.0.1", "--port", "0"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None
        listening = re.search(r"ws://\S+", server.stdout.readline())
        assert listening is not None
        yield listening.group(0)
    finally:
        server.terminate()
        server.wait()


_LIMITS = BrowserLimits(cap=3, wait_seconds=5, idle_seconds=300)


@asynccontextmanager
async def browsers(
    server: str, proxy_address: str, limits: BrowserLimits = _LIMITS
) -> AsyncIterator[BrowserHub]:
    """A served hub whose browsers run on *server*, and send every request through the proxy at
    *proxy_address*."""
    options = urlencode({"launch-options": json.dumps({"proxy": {"server": proxy_address}})})
    with TemporaryDirectory() as directory:
        async with browser_hub(Path(directory) / "hub", limits, f"{server}?{options}") as hub:
            yield hub


@contextmanager
def as_instance(hub: BrowserHub, instance_id: UUID) -> Iterator[HubBrowser]:
    """The browser service as the instance reaches it, on its hub route with its control
    token."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(HUB_URL_VARIABLE, hub.route(instance_id).replace("http", "ws", 1))
        patch.setenv(CONTROL_TOKEN_VARIABLE, hub.token(instance_id))
        yield HubBrowser()


@asynccontextmanager
async def on_the_hub(server: str, proxy_address: str) -> AsyncIterator[HubBrowser]:
    """The browser service as alice reaches it, on a hub whose browsers run on *server*."""
    async with browsers(server, proxy_address) as hub:
        with as_instance(hub, hub.alice) as browser:
            yield browser


@pytest.mark.browser
def test_an_interactive_snapshot_holds_refs_to_what_can_be_acted_on_and_respects_the_cap(
    browser_server,
) -> None:
    async def scenario() -> tuple[int, str, str, str, str]:
        async with (
            site() as port,
            proxy(port) as address,
            on_the_hub(browser_server, address) as browser,
        ):
            turn = uuid4()
            await browser.open(turn)
            try:
                page = await browser.run(turn, Navigate(f"http://site.test:{port}/"))
                interactive = await browser.snapshot(turn, interactive=True, max_chars=2_000)
                full = await browser.snapshot(turn, interactive=False, max_chars=2_000)
                ref = re.search(r"paragraph \[ref=(e\d+)\]", full.text)
                assert ref is not None, full.text
                paragraph = await browser.text(turn, ref.group(1))
            finally:
                await browser.close(turn)
            return port, page.title, interactive.text, full.text, paragraph.text

    port, title, interactive, full, paragraph = asyncio.run(scenario())

    assert title == "Applications"
    assert re.search(r'link "Next page" \[ref=e\d+\]', interactive)
    assert f"http://site.test:{port}/next" in interactive
    assert re.search(r'textbox "Search" \[ref=e\d+\]', interactive)
    assert re.search(r'button "Go" \[ref=e\d+\]', interactive)
    assert "listitem" not in interactive
    assert interactive.endswith("[The page was cut at 2,000 characters.]")
    assert full.endswith("[The page was cut at 2,000 characters.]")
    assert "You have 3 open applications." in full
    assert paragraph == "You have 3 open applications."


@pytest.mark.browser
def test_the_service_refuses_an_address_that_is_not_public(browser_server) -> None:
    async def scenario() -> None:
        async with (
            site() as port,
            proxy(port) as address,
            on_the_hub(browser_server, address) as browser,
        ):
            turn = uuid4()
            await browser.open(turn)
            try:
                await browser.run(turn, Navigate("http://10.255.255.1/"))
            finally:
                await browser.close(turn)

    with pytest.raises(ValueError, match="not a public address"):
        asyncio.run(scenario())


@pytest.mark.browser
def test_two_turns_get_contexts_that_share_no_cookie(browser_server) -> None:
    async def scenario() -> tuple[str, str]:
        async with (
            site() as port,
            proxy(port) as address,
            on_the_hub(browser_server, address) as browser,
        ):
            first, second = uuid4(), uuid4()
            await browser.open(first)
            await browser.open(second)
            try:
                await browser.run(first, Navigate(f"http://site.test:{port}/sign-in"))
                await browser.run(first, Navigate(f"http://site.test:{port}/cookies"))
                await browser.run(second, Navigate(f"http://site.test:{port}/cookies"))
                return (await browser.text(first, None)).text, (
                    await browser.text(second, None)
                ).text
            finally:
                await browser.close(first)
                await browser.close(second)

    signed_in, other = asyncio.run(scenario())

    assert signed_in == f"session: {_SESSION}"
    assert other == "session: none"


#: Runs one action on the form, by the ref of the element the pattern finds in its snapshot.
type Act = Callable[[str, Callable[[str], BrowserAction]], Awaitable[tuple[str, Page]]]


def _on_the_form(server: str, scenario: Callable[[Act], Awaitable[None]]) -> None:
    """Run *scenario* on the form page, giving it a way to act on an element and read what the
    page says after."""

    async def run() -> None:
        async with site() as port, proxy(port) as address, on_the_hub(server, address) as browser:
            turn = uuid4()
            await browser.open(turn)
            try:
                await browser.run(turn, Navigate(f"http://site.test:{port}/form"))

                async def act(
                    pattern: str, action: Callable[[str], BrowserAction]
                ) -> tuple[str, Page]:
                    tree = await browser.snapshot(turn, interactive=False, max_chars=20_000)
                    ref = re.search(rf"{pattern}.*?\[ref=(e\d+)\]", tree.text)
                    assert ref is not None, tree.text
                    page = await browser.run(turn, action(ref.group(1)))
                    said = await browser.text(turn, None)
                    return said.text.splitlines()[0], page

                await scenario(act)
            finally:
                await browser.close(turn)

    asyncio.run(run())


@pytest.mark.browser
def test_a_click_a_typed_value_a_selection_and_a_key_change_the_page(browser_server) -> None:
    said: list[str] = []

    async def scenario(act: Act) -> None:
        for pattern, action in [
            ('textbox "Name"', lambda ref: Type(ref, "Ada")),
            ('textbox "Name"', lambda _: Press("Enter")),
            ('combobox "Role"', lambda ref: Select(ref, "Manager")),
            ('button "Apply"', Click),
        ]:
            said.append((await act(pattern, action))[0])

    _on_the_form(browser_server, scenario)

    assert said == ["Name: Ada", "Sent Ada", "Role: pm", "Applied"]


@pytest.mark.browser
def test_a_click_on_a_covered_element_fails_with_what_covers_it(browser_server) -> None:
    async def scenario(act: Act) -> None:
        await act('button "Withdraw"', Click)

    with pytest.raises(ValueError, match=r"covered by <div.*>Cookies</div>"):
        _on_the_form(browser_server, scenario)


@pytest.mark.browser
def test_typing_into_a_password_field_is_refused(browser_server) -> None:
    async def scenario(act: Act) -> None:
        await act('textbox "Password"', lambda ref: Type(ref, "hunter2"))

    with pytest.raises(ValueError, match="password field"):
        _on_the_form(browser_server, scenario)


@pytest.mark.browser
def test_a_key_press_types_no_character_into_a_password_field(browser_server) -> None:
    said: list[str] = []

    async def scenario(act: Act) -> None:
        await act('textbox "Password"', Click)
        said.append((await act('textbox "Password"', lambda _: Press("Enter")))[0])
        with pytest.raises(ValueError, match="password field"):
            await act('textbox "Password"', lambda _: Press("a"))

    _on_the_form(browser_server, scenario)

    assert said == ["Key: Enter"]


@asynccontextmanager
async def with_a_vault_entry(
    server: str, proxy_address: str, entry_site: str
) -> AsyncIterator[HubBrowser]:
    """The browser service as alice reaches it, whose vault holds the entry "Site" for
    *entry_site*."""
    async with browsers(server, proxy_address) as hub:
        hub.hub.vault.add(
            VaultEntryAddCommand.model_validate(
                {
                    "instance_id": hub.alice,
                    "name": "Site",
                    "site": entry_site,
                    "username": _USERNAME,
                    "password": _PASSWORD,
                    "totp_seed": _SEED,
                }
            )
        )
        with as_instance(hub, hub.alice) as browser:
            yield browser


async def _ref(browser: HubBrowser, turn: UUID, label: str) -> str:
    tree = await browser.snapshot(turn, interactive=True, max_chars=20_000)
    ref = re.search(rf'"{label}" \[ref=(e\d+)\]', tree.text)
    assert ref is not None, tree.text
    return ref.group(1)


_SIGN_IN_FIELDS = [
    ("Email", VaultField.USERNAME),
    ("Password", VaultField.PASSWORD),
    ("Code", VaultField.TOTP),
]


async def _signed_in_by_the_vault(server: str, method: str) -> tuple[str, Page, str]:
    """Fill the sign-in form sent by *method* from the vault and send it: what a snapshot of the
    filled form shows, the page the form sent the browser to, and what that page says."""
    async with (
        site() as port,
        proxy(port) as address,
        with_a_vault_entry(server, address, f"http://site.test:{port}") as browser,
    ):
        turn = uuid4()
        await browser.open(turn)
        try:
            await browser.run(turn, Navigate(f"http://site.test:{port}/login?method={method}"))
            for label, field in _SIGN_IN_FIELDS:
                await browser.run(
                    turn, FillCredential(await _ref(browser, turn, label), "Site", field)
                )
            filled = await browser.snapshot(turn, interactive=False, max_chars=20_000)
            page = await browser.run(turn, Click(await _ref(browser, turn, "Sign in")))
            said = await browser.text(turn, None)
        finally:
            await browser.close(turn)
    return filled.text, page, said.text


@pytest.mark.browser
def test_a_vault_fill_signs_in_and_the_turn_never_reads_a_value(browser_server) -> None:
    filled, _, said = asyncio.run(_signed_in_by_the_vault(browser_server, "post"))

    assert said == "Signed in as [a vault value]. Password right. Code right."
    assert _USERNAME not in filled
    assert _PASSWORD not in filled
    assert 'textbox "Password"' in filled


@pytest.mark.browser
def test_a_sign_in_form_sent_by_get_leaves_no_value_in_the_url(browser_server) -> None:
    _, page, said = asyncio.run(_signed_in_by_the_vault(browser_server, "get"))

    assert said == "Signed in as [a vault value]. Password right. Code right."
    assert "username=[a vault value]" in page.url
    assert "password=[a vault value]" in page.url
    assert "alice" not in page.url
    assert "horse" not in page.url


def _fill_refused(
    server: str, entry_site: str, label: str, field: VaultField, ref: str | None = None
) -> None:
    """Fill the sign-in form's field *label*, or the field *ref*, with *field*."""

    async def scenario() -> None:
        async with (
            site() as port,
            proxy(port) as address,
            with_a_vault_entry(server, address, entry_site.format(port=port)) as browser,
        ):
            turn = uuid4()
            await browser.open(turn)
            try:
                await browser.run(turn, Navigate(f"http://site.test:{port}/login"))
                filled = ref or await _ref(browser, turn, label)
                await browser.run(turn, FillCredential(filled, "Site", field))
            finally:
                await browser.close(turn)

    asyncio.run(scenario())


@pytest.mark.browser
def test_a_vault_fill_on_another_site_is_refused(browser_server) -> None:
    with pytest.raises(ValueError, match=r"fills pages on https://site\.test only"):
        _fill_refused(browser_server, "https://site.test", "Password", VaultField.PASSWORD)


@pytest.mark.browser
def test_a_vault_fill_into_a_child_frame_is_refused(browser_server) -> None:
    with pytest.raises(ValueError, match="in a frame of the page"):
        _fill_refused(
            browser_server, "http://site.test:{port}", "", VaultField.PASSWORD, ref="f1e3"
        )


@pytest.mark.browser
@pytest.mark.parametrize(
    ("label", "field", "refused"),
    [
        ("Email", VaultField.PASSWORD, "not a password field"),
        ("Password", VaultField.USERNAME, "is a password field"),
        ("Password", VaultField.TOTP, "is a password field"),
    ],
)
def test_a_password_fills_only_a_password_field_and_nothing_else_does(
    browser_server, label: str, field: VaultField, refused: str
) -> None:
    with pytest.raises(ValueError, match=refused):
        _fill_refused(browser_server, "http://site.test:{port}", label, field)


@pytest.mark.browser
def test_a_download_that_begins_after_its_click_returns_with_the_next_action(
    browser_server,
) -> None:
    pages: list[Page] = []

    async def scenario(act: Act) -> None:
        pages.append((await act('link "Export"', Click))[1])
        await asyncio.sleep(2)
        pages.append((await act('link "Export"', lambda ref: Scroll("down", ref)))[1])

    _on_the_form(browser_server, scenario)

    assert [page.downloads for page in pages] == [(), (Download("export.csv", _REPORT),)]


@pytest.mark.browser
def test_a_click_that_downloads_a_file_returns_it(browser_server) -> None:
    pages: list[Page] = []

    async def scenario(act: Act) -> None:
        pages.append((await act('link "Report"', Click))[1])

    _on_the_form(browser_server, scenario)

    assert pages[0].downloads == (Download("report.csv", _REPORT),)


@pytest.mark.browser
def test_an_upload_attaches_the_file(browser_server, tmp_path: Path) -> None:
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.7")
    said: list[str] = []

    async def scenario(act: Act) -> None:
        said.append((await act('button "CV"', lambda ref: Upload(ref, cv)))[0])

    _on_the_form(browser_server, scenario)

    assert said == ["Attached cv.pdf as application/pdf"]


async def _watched(
    hub: BrowserHub, subscription: str, takeover: Mapping[str, object]
) -> Stream[ContractModel]:
    """The user's live view of a takeover, on the hub."""
    stream = await hub.hub.dispatcher.subscribe(subscription, takeover, HUB_SCOPES)
    assert not isinstance(stream, ErrorEnvelope), stream
    return stream


async def _first_frame(stream: Stream[ContractModel]) -> bytes:
    async with asyncio.timeout(10):
        frame = await anext(stream.items)
    assert isinstance(frame, TakeoverFrame)
    return base64.b64decode(frame.data)


@pytest.mark.browser
def test_the_user_watches_a_turns_takeover_on_the_hub_and_drives_its_page(browser_server) -> None:
    async def scenario() -> tuple[bytes, list[ThreadTakeoverInputResult | ErrorEnvelope], str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address) as hub,
        ):
            with as_instance(hub, hub.alice) as browser:
                turn = uuid4()
                await browser.open(turn)
                try:
                    await browser.run(turn, Navigate(f"http://site.test:{port}/pad"))
                    takeover = {"instance_id": hub.alice, "turn_id": turn}
                    watched = await _watched(hub, THREAD_TAKEOVER_START.name, takeover)
                    frame = await _first_frame(watched)
                    client = hub_client(hub.hub)
                    driven = [
                        await client.call(
                            THREAD_TAKEOVER_INPUT,
                            ThreadTakeoverInputCommand(**takeover, input=user_input),
                        )
                        for user_input in [
                            TakeoverPointer(action="move", x=200, y=150),
                            TakeoverPointer(action="down", x=200, y=150),
                            TakeoverPointer(action="up", x=200, y=150),
                            TakeoverKey(key="a"),
                            TakeoverKey(key="Enter"),
                            TakeoverWheel(delta_x=0, delta_y=100),
                        ]
                    ]
                    await watched.aclose()
                    said = await browser.text(turn, None)
                finally:
                    await browser.close(turn)
        return frame, driven, said.text

    frame, driven, said = asyncio.run(scenario())

    assert frame.startswith(b"\xff\xd8")
    assert driven == [ThreadTakeoverInputResult()] * 6
    assert said == "Clicked at 200,150 a Enter"


@pytest.mark.browser
def test_the_live_view_opens_the_page_of_a_turn_that_parked_with_no_browser_free(
    browser_server,
) -> None:
    async def scenario() -> tuple[bytes, str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address, _ONE_BROWSER) as hub,
        ):
            parked = uuid4()
            with as_instance(hub, hub.alice) as alices:
                holding = await _open(alices, port)
                with pytest.raises(ValueError, match="browser cap of 1 is reached"):
                    await alices.open(parked)
                await alices.close(holding)
                watched = await _watched(
                    hub, THREAD_TAKEOVER_START.name, {"instance_id": hub.alice, "turn_id": parked}
                )
                frame = await _first_frame(watched)
                await watched.aclose()
                # The turn ends, and its close reaches the page the hub opened for the user.
                await alices.close(parked)
            return frame, await _read_by_bob(hub, port)

    frame, read_by_bob = asyncio.run(scenario())

    assert frame.startswith(b"\xff\xd8")
    assert "You have 3 open applications." in read_by_bob


@pytest.mark.browser
def test_finishing_a_turns_takeover_as_a_profile_saves_it_and_resumes_the_turn_with_it(
    browser_server,
) -> None:
    thread, turn, takeover = uuid4(), uuid4(), uuid4()
    resumed: list[ThreadTakeoverResumeCommand] = []

    async def resume(command: ThreadTakeoverResumeCommand) -> AcceptedResult:
        resumed.append(command)
        return AcceptedResult(thread_id=command.thread_id, turn_id=turn, sequence=7)

    instance = Dispatcher()
    instance.register(THREAD_TAKEOVER_RESUME, resume)

    async def scenario() -> tuple[object, BrowserProfileListResult, str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address) as hub,
            instance_served(instance, hub.token(hub.alice)) as private,
        ):
            hub.instances.addresses[str(hub.alice)] = f"http://{private.host}:{private.port}"
            with as_instance(hub, hub.alice) as browser:
                await browser.open(turn)
                try:
                    await browser.run(turn, Navigate(f"http://site.test:{port}/sign-in"))
                    finished = await hub_client(hub.hub).call(
                        THREAD_TAKEOVER_FINISH,
                        ThreadTakeoverFinishCommand(
                            instance_id=hub.alice,
                            thread_id=thread,
                            turn_id=turn,
                            takeover_id=takeover,
                            profile="Site",
                        ),
                    )
                    # The resumed turn goes on in the page the user left, signed in.
                    await browser.run(turn, Navigate(f"http://site.test:{port}/refresh"))
                finally:
                    await browser.close(turn)
                profiles = await browser.profiles()
                after = uuid4()
                await browser.open(after, "Site")
                try:
                    await browser.run(after, Navigate(f"http://site.test:{port}/cookies"))
                    signed_in = (await browser.text(after, None)).text
                finally:
                    await browser.close(after)
        return finished, profiles, signed_in

    finished, profiles, signed_in = asyncio.run(scenario())

    assert finished == AcceptedResult(thread_id=thread, turn_id=turn, sequence=7)
    assert resumed == [ThreadTakeoverResumeCommand(thread_id=thread, takeover_id=takeover)]
    assert profiles == BrowserProfileListResult(
        profiles=(BrowserProfile(name="Site", site="site.test"),)
    )
    assert signed_in == f"session: {_SESSION}-renewed"


async def _stopped(hub: BrowserHub, instance_id: UUID) -> None:
    client = hub_client(hub.hub)
    # The instance has no drain to answer, so its container goes down at once.
    stopping = await client.call(
        INSTANCE_STOP, InstanceStopCommand(instance_id=instance_id, force=True)
    )
    assert isinstance(stopping, LifecycleOperationResult)
    assert (await finished_operation(client, stopping)).state is OperationState.SUCCEEDED


async def _read_by_bob(hub: BrowserHub, port: int) -> str:
    """The site's page, as bob reads it in a browser of his own, which a takeover that kept its
    browser keeps from him under a cap of one."""
    browser_on(hub.hub, hub.bob)
    with as_instance(hub, hub.bob) as bobs:
        turn = await _open(bobs, port)
        try:
            return (await bobs.text(turn, None)).text
        finally:
            await bobs.close(turn)


_ONE_BROWSER = BrowserLimits(cap=1, wait_seconds=0.5, idle_seconds=300)


@pytest.mark.browser
def test_a_takeover_with_no_turn_saves_a_profile_while_the_instance_is_stopped(
    browser_server,
) -> None:
    async def scenario() -> tuple[bytes, object, object, object, str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address, _ONE_BROWSER) as hub,
        ):
            await _stopped(hub, hub.alice)
            takeover = {"instance_id": hub.alice, "takeover_id": uuid4()}
            watched = await _watched(
                hub,
                BROWSER_TAKEOVER_START.name,
                {**takeover, "url": f"http://site.test:{port}/sign-in"},
            )
            frame = await _first_frame(watched)
            client = hub_client(hub.hub)
            driven = await client.call(
                BROWSER_TAKEOVER_INPUT,
                BrowserTakeoverInputCommand(**takeover, input=TakeoverKey(key="Tab")),
            )
            saved = await client.call(
                BROWSER_TAKEOVER_FINISH, BrowserTakeoverFinishCommand(**takeover, profile="Site")
            )
            listed = await client.call(
                BROWSER_PROFILE_LIST, BrowserProfileListCommand(instance_id=hub.alice)
            )
            read_by_bob = await _read_by_bob(hub, port)
            await watched.aclose()
        return frame, driven, saved, listed, read_by_bob

    frame, driven, saved, listed, read_by_bob = asyncio.run(scenario())

    assert frame.startswith(b"\xff\xd8")
    assert driven == ThreadTakeoverInputResult()
    profile = BrowserProfile(name="Site", site="site.test")
    assert saved == profile
    assert listed == BrowserProfileListResult(profiles=(profile,))
    assert "You have 3 open applications." in read_by_bob


@pytest.mark.browser
def test_a_takeover_with_no_turn_closes_its_browser_when_nobody_watches_it(
    browser_server,
) -> None:
    async def scenario() -> tuple[object, str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address, _ONE_BROWSER) as hub,
        ):
            takeover = {"instance_id": hub.alice, "takeover_id": uuid4()}
            watched = await _watched(
                hub, BROWSER_TAKEOVER_START.name, {**takeover, "url": f"http://site.test:{port}/"}
            )
            await _first_frame(watched)
            await watched.aclose()
            late_finish = await hub_client(hub.hub).call(
                BROWSER_TAKEOVER_FINISH, BrowserTakeoverFinishCommand(**takeover, profile="Site")
            )
            return late_finish, await _read_by_bob(hub, port)

    late_finish, read_by_bob = asyncio.run(scenario())

    assert isinstance(late_finish, ErrorEnvelope)
    assert late_finish.code is ErrorCode.NOT_FOUND
    assert "You have 3 open applications." in read_by_bob


@pytest.mark.browser
def test_a_takeover_with_no_turn_left_while_its_page_loads_closes_its_browser(
    browser_server,
) -> None:
    stalled = asyncio.Event()

    async def scenario() -> str:
        async with (
            site(stalled) as port,
            proxy(port) as address,
            browsers(browser_server, address, _ONE_BROWSER) as hub,
        ):
            watching = asyncio.create_task(
                _watched(
                    hub,
                    BROWSER_TAKEOVER_START.name,
                    {
                        "instance_id": hub.alice,
                        "takeover_id": uuid4(),
                        "url": f"http://site.test:{port}/stall",
                    },
                )
            )
            async with asyncio.timeout(10):
                await stalled.wait()
            watching.cancel()
            with suppress(asyncio.CancelledError):
                await watching
            return await _read_by_bob(hub, port)

    assert "You have 3 open applications." in asyncio.run(scenario())


@pytest.mark.browser
def test_a_profile_saved_from_one_turns_browser_opens_the_next_signed_in(browser_server) -> None:
    async def scenario() -> tuple[BrowserProfile, str]:
        async with (
            site() as port,
            proxy(port) as address,
            on_the_hub(browser_server, address) as browser,
        ):
            first, second = uuid4(), uuid4()
            await browser.open(first)
            try:
                await browser.run(first, Navigate(f"http://site.test:{port}/sign-in"))
                saved = await browser.save_profile(first, "Site")
            finally:
                await browser.close(first)
            await browser.open(second, "Site")
            try:
                await browser.run(second, Navigate(f"http://site.test:{port}/cookies"))
                return saved, (await browser.text(second, None)).text
            finally:
                await browser.close(second)

    saved, signed_in = asyncio.run(scenario())

    assert saved == BrowserProfile(name="Site", site="site.test")
    assert signed_in == f"session: {_SESSION}"


@pytest.mark.browser
def test_a_cookie_the_site_renews_during_a_turn_is_in_the_profile_after_it(
    browser_server,
) -> None:
    async def turn(browser: HubBrowser, profile: str | None, url: str) -> str:
        turn = uuid4()
        await browser.open(turn, profile)
        try:
            await browser.run(turn, Navigate(url))
            if profile is None:
                await browser.save_profile(turn, "Site")
            return (await browser.text(turn, None)).text
        finally:
            await browser.close(turn)

    async def scenario() -> str:
        async with (
            site() as port,
            proxy(port) as address,
            on_the_hub(browser_server, address) as browser,
        ):
            await turn(browser, None, f"http://site.test:{port}/sign-in")
            await turn(browser, "Site", f"http://site.test:{port}/refresh")
            return await turn(browser, "Site", f"http://site.test:{port}/cookies")

    assert asyncio.run(scenario()) == f"session: {_SESSION}-renewed"


@pytest.mark.browser
def test_the_route_lets_through_each_read_the_browser_tools_make(browser_server) -> None:
    async def scenario() -> tuple[list[str], bytes]:
        async with (
            site() as port,
            proxy(port) as address,
            on_the_hub(browser_server, address) as browser,
        ):
            turn = uuid4()
            await browser.open(turn)
            try:
                await browser.run(turn, Navigate(f"http://site.test:{port}/"))
                tree = await browser.snapshot(turn, interactive=True, max_chars=20_000)
                ref = re.search(r'link "Company 150" \[ref=(e\d+)\]', tree.text)
                assert ref is not None, tree.text
                pages = [
                    await browser.run(turn, Scroll("down")),
                    await browser.run(turn, Scroll("down", ref.group(1))),
                    await browser.run(turn, Wait("Company 150", 5)),
                    await browser.run(turn, Wait(None, 5)),
                ]
                return [page.title for page in pages], await browser.screenshot(turn)
            finally:
                await browser.close(turn)

    titles, screenshot = asyncio.run(scenario())

    assert titles == ["Applications"] * 4
    assert screenshot.startswith(b"\x89PNG")


async def _open(browser: HubBrowser, port: int) -> UUID:
    """A turn whose browser is open on the site."""
    turn = uuid4()
    await browser.open(turn)
    await browser.run(turn, Navigate(f"http://site.test:{port}/"))
    return turn


@pytest.mark.browser
def test_a_call_naming_another_instances_turn_is_refused(browser_server) -> None:
    async def scenario() -> tuple[UUID, list[dict[str, object]], dict[str, object], str]:
        async with site() as port, proxy(port) as address, browsers(browser_server, address) as hub:
            browser_on(hub.hub, hub.bob)
            with as_instance(hub, hub.alice) as alices:
                turn = await _open(alices, port)
                named: dict[str, object] = {"turn_id": str(turn)}
                refused = [
                    error(await hub.call(hub.bob, method, params))
                    for method, params in [
                        ("browser.text", named),
                        ("browser.screenshot", named),
                        ("browser.run", {**named, "action": {"type": "press", "key": "a"}}),
                    ]
                ]
                closed = result(await hub.call(hub.bob, "browser.close", named))
                still_open = (await alices.text(turn, None)).text
                await alices.close(turn)
            return turn, refused, closed, still_open

    turn, refused, closed, still_open = asyncio.run(scenario())

    assert [(answer["code"], answer["message"]) for answer in refused] == [
        (ErrorCode.NOT_FOUND.value, f'Turn "{turn}" has no browser open.')
    ] * 3
    assert closed == {}
    assert "You have 3 open applications." in still_open


@pytest.mark.browser
def test_with_the_cap_full_a_turn_waits_until_a_browser_closes(browser_server) -> None:
    limits = BrowserLimits(cap=1, wait_seconds=0.05, idle_seconds=300)

    async def scenario() -> tuple[dict[str, object], str, str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address, limits) as hub,
        ):
            with as_instance(hub, hub.alice) as browser:
                first = await _open(browser, port)
                refused = error(await hub.open(hub.alice, uuid4()))
                with pytest.raises(ValueError, match="cap of 1") as raised:
                    await browser.open(uuid4())
                await browser.close(first)
                second = await _open(browser, port)
                read = await browser.text(second, None)
                await browser.close(second)
            return refused, str(raised.value), read.text

    refused, raised, read = asyncio.run(scenario())

    assert refused["code"] == ErrorCode.RESOURCE_EXHAUSTED.value
    assert refused["retryable"] is True
    assert refused["message"] == (
        "The hub's browser cap of 1 is reached, and no browser came free within 0.05 seconds. "
        "Try again later."
    )
    assert raised == f"The hub refused the browser: {refused['message']}"
    assert "You have 3 open applications." in read


@pytest.mark.browser
def test_a_turns_browser_left_idle_closes_and_goes_back_to_the_cap(browser_server) -> None:
    limits = BrowserLimits(cap=1, wait_seconds=5, idle_seconds=0.05, turn_idle_seconds=0.2)

    async def scenario() -> tuple[str, str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address, limits) as hub,
        ):
            with as_instance(hub, hub.alice) as browser:
                idle = await _open(browser, port)
                # The service stops only once no browser is open.
                await asyncio.wait_for(hub.runtime.stopped.wait(), 10)
                with pytest.raises(ValueError, match="has no browser open") as closed:
                    await browser.text(idle, None)
                fresh = await _open(browser, port)
                read = await browser.text(fresh, None)
                await browser.close(fresh)
            return str(closed.value), read.text

    closed, read = asyncio.run(scenario())

    assert "has no browser open" in closed
    assert "You have 3 open applications." in read


@pytest.mark.browser
def test_a_stopped_instances_browsers_close_and_go_back_to_the_cap(browser_server) -> None:
    limits = BrowserLimits(cap=1, wait_seconds=0.05, idle_seconds=300)

    async def scenario() -> tuple[OperationState, str]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address, limits) as hub,
        ):
            browser_on(hub.hub, hub.bob)
            with as_instance(hub, hub.alice) as alices:
                await _open(alices, port)
            client = hub_client(hub.hub)
            # The instance has no drain to answer, so its container goes down at once.
            stopping = await client.call(
                INSTANCE_STOP, InstanceStopCommand(instance_id=hub.alice, force=True)
            )
            assert isinstance(stopping, LifecycleOperationResult)
            stopped = await finished_operation(client, stopping)
            with as_instance(hub, hub.bob) as bobs:
                turn = await _open(bobs, port)
                read = await bobs.text(turn, None)
                await bobs.close(turn)
            return stopped.state, read.text

    stopped, read = asyncio.run(scenario())

    assert stopped is OperationState.SUCCEEDED
    assert "You have 3 open applications." in read


@pytest.mark.browser
def test_the_hub_starts_the_service_on_the_first_browser_and_stops_it_once_idle(
    browser_server,
) -> None:
    limits = BrowserLimits(cap=3, wait_seconds=5, idle_seconds=0.05)

    async def scenario() -> tuple[int, int, int]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address, limits) as hub,
        ):
            with as_instance(hub, hub.alice) as browser:
                first, second = await _open(browser, port), await _open(browser, port)
                starts = hub.runtime.starts
                await browser.close(first)
                await browser.close(second)
                await asyncio.wait_for(hub.runtime.stopped.wait(), 10)
                await browser.close(await _open(browser, port))
            return starts, hub.runtime.stops, hub.runtime.starts

    assert asyncio.run(scenario()) == (1, 1, 2)


@pytest.mark.browser
def test_a_service_that_died_starts_again_on_the_next_browser(browser_server) -> None:
    async def scenario() -> int:
        async with site() as port, proxy(port) as address, browsers(browser_server, address) as hub:
            with as_instance(hub, hub.alice) as browser:
                first = await _open(browser, port)
                hub.runtime.up = False
                second = await _open(browser, port)
                await browser.close(first)
                await browser.close(second)
            return hub.runtime.starts

    assert asyncio.run(scenario()) == 2


def _holding(directory: Path, secret: str) -> list[Path]:
    """The files under *directory* that hold *secret*."""
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and secret.encode() in path.read_bytes()
    ]


@pytest.mark.browser
def test_a_profile_is_listed_and_its_cookie_stays_with_the_hub(browser_server) -> None:
    async def scenario() -> tuple[object, object, list[Path], list[tuple[Path, int]], Path]:
        async with site() as port, proxy(port) as address, browsers(browser_server, address) as hub:
            with as_instance(hub, hub.alice) as browser:
                turn = uuid4()
                await browser.open(turn)
                await browser.run(turn, Navigate(f"http://site.test:{port}/sign-in"))
                await browser.save_profile(turn, "Site")
                await browser.close(turn)
                listed = await browser.profiles()
            as_the_user = await hub_client(hub.hub).call(
                BROWSER_PROFILE_LIST, BrowserProfileListCommand(instance_id=hub.alice)
            )
            record = hub.hub.registry.instance(hub.alice)
            assert record is not None
            profiles = hub.hub.directory / "browser-profiles"
            return (
                listed,
                as_the_user,
                _holding(record.path, _SESSION),
                [
                    (path, path.stat().st_mode & 0o777)
                    for path in _holding(hub.hub.directory, _SESSION)
                ],
                profiles,
            )

    listed, as_the_user, in_the_instance, in_the_hub, profiles = asyncio.run(scenario())

    saved = BrowserProfileListResult(profiles=(BrowserProfile(name="Site", site="site.test"),))
    assert listed == as_the_user == saved
    assert in_the_instance == []
    assert [(path.parent, mode) for path, mode in in_the_hub] == [(profiles, 0o600)]


@pytest.mark.browser
def test_a_download_over_the_cap_is_refused(browser_server) -> None:
    async def scenario(act: Act) -> None:
        await act('link "Archive"', Click)

    with pytest.raises(ValueError, match="over the browser's cap"):
        _on_the_form(browser_server, scenario)


def test_an_upload_over_the_cap_never_reaches_the_hub(tmp_path: Path) -> None:
    archive = tmp_path / "archive.zip"
    with archive.open("wb") as file:
        file.truncate(BROWSER_FILE_BYTES + 1)

    with pytest.raises(ValueError, match="over the browser's cap"):
        asyncio.run(HubBrowser().run(uuid4(), Upload("e1", archive)))


def test_a_turn_that_opened_no_browser_closes_without_calling_the_hub() -> None:
    asyncio.run(HubBrowser().close(uuid4()))


@pytest.mark.browser
def test_a_takeover_after_a_new_tab_opens_watches_the_new_tab(browser_server) -> None:
    async def scenario() -> tuple[bytes, bytes]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address) as hub,
        ):

            async def first_frame(turn: UUID) -> bytes:
                takeover = {"instance_id": hub.alice, "turn_id": turn}
                watched = await _watched(hub, THREAD_TAKEOVER_START.name, takeover)
                try:
                    return await _first_frame(watched)
                finally:
                    await watched.aclose()

            with as_instance(hub, hub.alice) as browser:
                alone, opener = uuid4(), uuid4()
                await browser.open(alone)
                await browser.run(alone, Navigate(f"http://site.test:{port}/blue"))
                blue = await first_frame(alone)
                await browser.close(alone)
                await browser.open(opener)
                await browser.run(opener, Navigate(f"http://site.test:{port}/opener"))
                await first_frame(opener)
                tree = await browser.snapshot(opener, interactive=True, max_chars=2_000)
                ref = re.search(r'link "Blue" \[ref=(e\d+)\]', tree.text)
                assert ref is not None, tree.text
                await browser.run(opener, Click(ref.group(1)))
                await browser.run(opener, Wait(None, 5))
                watched = await first_frame(opener)
                await browser.close(opener)
                return blue, watched

    blue, watched = asyncio.run(scenario())

    assert watched == blue


@pytest.mark.browser
def test_a_close_sent_while_the_open_is_pending_closes_the_browser(browser_server) -> None:
    async def scenario() -> tuple[list[str], dict[str, object]]:
        async with (
            site() as port,
            proxy(port) as address,
            browsers(browser_server, address) as hub,
            aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {hub.token(hub.alice)}"}
            ) as session,
            session.ws_connect(hub.route(hub.alice)) as socket,
        ):
            turn = {"turn_id": str(uuid4())}
            for frame_id, method in [("open", "browser.open"), ("close", "browser.close")]:
                await socket.send_json(
                    {"type": "call", "id": frame_id, "method": method, "params": turn}
                )
            answered = [(await frame(socket))["id"] for _ in range(2)]
            return sorted(str(frame_id) for frame_id in answered), await hub.call(
                hub.alice, "browser.text", turn
            )

    answered, read = asyncio.run(scenario())

    assert answered == ["close", "open"]
    assert error(read)["code"] == ErrorCode.NOT_FOUND.value


def test_a_turn_whose_open_was_cancelled_still_closes_its_browser_on_the_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    reached = asyncio.Event()

    async def call_hub(method: str, params: dict[str, object], named: str) -> dict[str, object]:
        called.append(method)
        if method == "browser.open":
            reached.set()
            await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(hub_browser, "call_hub", call_hub)

    async def scenario() -> None:
        browser, turn = HubBrowser(), uuid4()
        opening = asyncio.create_task(browser.open(turn))
        await reached.wait()
        opening.cancel()
        with suppress(asyncio.CancelledError):
            await opening
        await browser.close(turn)

    asyncio.run(scenario())

    assert called == ["browser.open", "browser.close"]
