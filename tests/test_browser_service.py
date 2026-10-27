"""The browser service: its proxy refuses every address that is not public, and the real adapter
reads and acts on a page through Playwright's browser server (ADR 0113)."""

import asyncio
import json
import re
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import pytest
from aiohttp import web
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from kinby.contracts import BrowserOpenResult, BrowserToken
from kinby.hub.browser_service import serve_proxy
from kinby.plugins.browser import (
    BrowserAction,
    Click,
    Download,
    Navigate,
    Page,
    Press,
    Scroll,
    Select,
    Type,
    Upload,
)
from kinby.plugins.defaults.web import REFUSED_HEADER, RefusedUrl, public_address
from kinby.plugins.playwright_browser import PlaywrightBrowser

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
<input type="file" aria-label="CV" onchange="said.textContent = 'Attached ' + this.files[0].name">
</body></html>"""
_REPORT = b"company,status\nAcme,open\n"


async def _page(request: web.Request) -> web.Response:
    return web.Response(text=_PAGE.format(items=_ITEMS), content_type="text/html")


async def _form(request: web.Request) -> web.Response:
    return web.Response(text=_FORM, content_type="text/html")


async def _report(request: web.Request) -> web.Response:
    """The report, slow to answer, as a loaded server is, so its download begins after the click
    that asked for it returns."""
    await asyncio.sleep(0.2)
    return web.Response(body=_REPORT, content_type="text/csv")


async def _export(request: web.Request) -> web.Response:
    """An export, slower to answer than a click waits for its download."""
    await asyncio.sleep(1.5)
    return web.Response(body=_REPORT, content_type="text/csv")


async def _set_cookie(request: web.Request) -> web.Response:
    response = web.Response(text="<p>Signed in.</p>", content_type="text/html")
    response.set_cookie("session", "alice")
    return response


async def _cookies(request: web.Request) -> web.Response:
    sent = request.cookies.get("session", "none")
    return web.Response(text=f"<p>session: {sent}</p>", content_type="text/html")


@asynccontextmanager
async def site() -> AsyncIterator[int]:
    """The test pages, on a local port. The proxy lets the browser reach this port alone."""
    application = web.Application()
    application.router.add_get("/", _page)
    application.router.add_get("/form", _form)
    application.router.add_get("/report.csv", _report)
    application.router.add_get("/export.csv", _export)
    application.router.add_get("/sign-in", _set_cookie)
    application.router.add_get("/cookies", _cookies)
    runner = web.AppRunner(application)
    await runner.setup()
    served = web.TCPSite(runner, "127.0.0.1", 0)
    await served.start()
    try:
        yield served.port
    finally:
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


def _adapter(server: str, proxy_address: str) -> PlaywrightBrowser:
    """The adapter, given the address the hub's route gives, which sends the browser's every
    request through the service's proxy."""
    options = quote(json.dumps({"proxy": {"server": proxy_address}}))

    async def lease(turn: UUID) -> BrowserOpenResult:
        return BrowserOpenResult(
            address=f"{server}?launch-options={options}", token=BrowserToken("token")
        )

    return PlaywrightBrowser(lease)


@pytest.mark.browser
def test_an_interactive_snapshot_holds_refs_to_what_can_be_acted_on_and_respects_the_cap(
    browser_server,
) -> None:
    async def scenario() -> tuple[int, str, str, str, str]:
        async with site() as port, proxy(port) as address:
            browser, turn = _adapter(browser_server, address), uuid4()
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
        async with site() as port, proxy(port) as address:
            browser, turn = _adapter(browser_server, address), uuid4()
            await browser.open(turn)
            try:
                await browser.run(turn, Navigate("http://10.255.255.1/"))
            finally:
                await browser.close(turn)

    with pytest.raises(RefusedUrl, match="not a public address"):
        asyncio.run(scenario())


@pytest.mark.browser
def test_two_turns_get_contexts_that_share_no_cookie(browser_server) -> None:
    async def scenario() -> tuple[str, str]:
        async with site() as port, proxy(port) as address:
            browser, first, second = _adapter(browser_server, address), uuid4(), uuid4()
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

    assert signed_in == "session: alice"
    assert other == "session: none"


#: Runs one action on the form, by the ref of the element the pattern finds in its snapshot.
type Act = Callable[[str, Callable[[str], BrowserAction]], Awaitable[tuple[str, Page]]]


def _on_the_form(server: str, scenario: Callable[[Act], Awaitable[None]]) -> None:
    """Run *scenario* on the form page, giving it a way to act on an element and read what the
    page says after."""

    async def run() -> None:
        async with site() as port, proxy(port) as address:
            browser, turn = _adapter(server, address), uuid4()
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

    with pytest.raises(PermissionError, match="password field"):
        _on_the_form(browser_server, scenario)


@pytest.mark.browser
def test_a_key_press_types_no_character_into_a_password_field(browser_server) -> None:
    said: list[str] = []

    async def scenario(act: Act) -> None:
        await act('textbox "Password"', Click)
        said.append((await act('textbox "Password"', lambda _: Press("Enter")))[0])
        with pytest.raises(PermissionError, match="password field"):
            await act('textbox "Password"', lambda _: Press("a"))

    _on_the_form(browser_server, scenario)

    assert said == ["Key: Enter"]


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

    assert said == ["Attached cv.pdf"]
