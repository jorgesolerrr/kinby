# /// script
# requires-python = ">=3.14"
# dependencies = ["playwright==1.63.0", "psutil==7.2.2"]
# ///
"""Drive Playwright's bundled Chromium against a kinby hub, with a stored browser session.

    uv run browser.py login <domain> [--token-stdin]   store the browser session for <domain>
    uv run browser.py run <domain> <script.py> [--headed]   run one check script
    uv run browser.py cleanup                          end what a killed run left behind

A check script runs with `page` (a Playwright Page whose relative URLs resolve against
https://<domain>) and `context` already bound. The browser session for each domain lives in
~/.kinby-e2e/<domain>.json. Exit code 3 means it is missing or the hub rejected it.

Every process this helper starts is recorded in ~/.kinby-e2e/pids.json and ended by PID.
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil
from playwright.sync_api import Browser, BrowserContext, sync_playwright

STATE = Path.home() / ".kinby-e2e"
PIDS = STATE / "pids.json"
SIGN_IN_NEEDED = 3
SIGN_IN_SECONDS = 600


@dataclass(frozen=True)
class Started:
    """A process this helper started. The creation time tells it from a later one on its PID."""

    pid: int
    created: float


def _recorded() -> list[Started]:
    if not PIDS.is_file():
        return []
    return [Started(**entry) for entry in json.loads(PIDS.read_text("utf-8"))]


def _write(started: list[Started]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    PIDS.write_text(json.dumps([asdict(entry) for entry in started]), "utf-8")


def _record(processes: Iterable[psutil.Process]) -> list[Started]:
    started = [Started(process.pid, process.create_time()) for process in processes]
    _write(_recorded() + started)
    return started


def _playwright_owned(process: psutil.Process) -> bool:
    """Playwright's driver and bundled browsers live in their own folders, never Chrome's."""
    parts = Path(process.exe()).parts
    return "ms-playwright" in parts or "playwright" in parts


def _end(started: list[Started]) -> None:
    """Kill each recorded process still alive, with its children, by PID and nothing else."""
    killed: list[psutil.Process] = []
    for entry in started:
        try:
            process = psutil.Process(entry.pid)
            if process.create_time() != entry.created:
                continue
            tree = [*process.children(recursive=True), process]
        except psutil.NoSuchProcess:
            continue
        for member in tree:
            with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                if _playwright_owned(member):
                    member.kill()
                    killed.append(member)
    psutil.wait_procs(killed, timeout=10)
    _write([entry for entry in _recorded() if entry not in started])


@contextmanager
def _chromium(*, headed: bool) -> Iterator[Browser]:
    """Launch bundled Chromium and end every process the launch started, however the run ends."""
    started: list[Started] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            # Everything under this process is ours: the Playwright driver and its Chromium.
            started = _record(psutil.Process().children(recursive=True))
            try:
                yield browser
            finally:
                browser.close()
    finally:
        _end(started)


def _session_file(domain: str) -> Path:
    return STATE / f"{domain}.json"


def _signed_in(context: BrowserContext) -> bool:
    return context.request.get("/auth/session").status == 204


def login(domain: str, *, token_stdin: bool) -> int:
    """Open a browser session on the hub and store it for later runs."""
    with _chromium(headed=not token_stdin) as browser:
        context = browser.new_context(base_url=f"https://{domain}")
        if token_stdin:
            response = context.request.post("/auth/login", data={"token": sys.stdin.read().strip()})
            if not response.ok:
                print(f"The hub refused the access token ({response.status}).", file=sys.stderr)
                return SIGN_IN_NEEDED
        else:
            page = context.new_page()
            page.goto("/")
            print("Sign in to the hub in the Chromium window. Waiting up to ten minutes.")
            deadline = time.monotonic() + SIGN_IN_SECONDS
            while not _signed_in(context):
                if time.monotonic() > deadline:
                    print("No sign-in arrived.", file=sys.stderr)
                    return SIGN_IN_NEEDED
                page.wait_for_timeout(2000)
        STATE.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=_session_file(domain))
    print(f"Stored the browser session for {domain}.")
    return 0


def run(domain: str, script: Path, *, headed: bool) -> int:
    """Run one check script against the hub, signed in with the stored browser session."""
    session = _session_file(domain)
    if not session.is_file():
        print(f"No stored browser session for {domain}. Run login.", file=sys.stderr)
        return SIGN_IN_NEEDED
    with _chromium(headed=headed) as browser:
        context = browser.new_context(
            base_url=f"https://{domain}",
            storage_state=session,
            viewport={"width": 1440, "height": 900},
        )
        if not _signed_in(context):
            session.unlink()
            print(f"The hub rejected the browser session for {domain}. Run login.", file=sys.stderr)
            return SIGN_IN_NEEDED
        runpy.run_path(str(script), init_globals={"page": context.new_page(), "context": context})
    return 0


def cleanup() -> int:
    """End the processes a killed run recorded and could not end itself."""
    started = _recorded()
    _end(started)
    print(f"Checked {len(started)} recorded processes.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="browser.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    login_parser = commands.add_parser("login", help="store the browser session for a domain")
    login_parser.add_argument("domain")
    login_parser.add_argument(
        "--token-stdin", action="store_true", help="read the access token from stdin"
    )
    run_parser = commands.add_parser("run", help="run a check script with `page` bound")
    run_parser.add_argument("domain")
    run_parser.add_argument("script", type=Path)
    run_parser.add_argument("--headed", action="store_true", help="show the window")
    commands.add_parser("cleanup", help="end processes a killed run left behind")
    args = parser.parse_args()
    match args.command:
        case "login":
            return login(args.domain, token_stdin=args.token_stdin)
        case "run":
            return run(args.domain, args.script, headed=args.headed)
        case _:
            return cleanup()


if __name__ == "__main__":
    sys.exit(main())
