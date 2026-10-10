"""Default workspace shell tool."""

import os
import signal
import subprocess
from collections.abc import Callable
from contextlib import suppress
from selectors import EVENT_READ, BaseSelector, DefaultSelector
from time import monotonic

from kinby.plugins import ToolContext, tool

_OUTPUT_CAP = 30_000
_DRAIN_SECONDS = 2
_POLL_SECONDS = 0.1


@tool(write=True)
def bash(command: str, context: ToolContext) -> str:
    """Run a Bash command in the workspace."""
    timeout_seconds = context.instance.manifest.tools.bash_timeout_seconds
    process = subprocess.Popen(
        ("bash", "-c", command),
        cwd=context.workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout_pipe = process.stdout
    stderr_pipe = process.stderr
    if stdout_pipe is None or stderr_pipe is None:
        raise RuntimeError("Bash pipes were not opened.")
    stdout = bytearray()
    stderr = bytearray()
    with stdout_pipe, stderr_pipe, DefaultSelector() as selector:
        selector.register(stdout_pipe, EVENT_READ, stdout)
        selector.register(stderr_pipe, EVENT_READ, stderr)
        exited = _read_until(selector, timeout_seconds, lambda: process.poll() is not None)
        if not exited:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        # A background child can hold the pipes open; once they close, its next write fails.
        _read_until(selector, _DRAIN_SECONDS, lambda: not selector.get_map())
    if not exited:
        output = _capped(_render_output(stdout, stderr))
        detail = f"\n{output}" if output else ""
        raise TimeoutError(f"Bash timed out after {timeout_seconds} seconds.{detail}")
    output = _render_output(stdout, stderr)
    if process.returncode:
        output = f"Exit code: {process.returncode}\n{output}"
    return _capped(output)


def _read_until(selector: BaseSelector, seconds: float, done: Callable[[], bool]) -> bool:
    deadline = monotonic() + seconds
    while not done():
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        for key, _ in selector.select(min(remaining, _POLL_SECONDS)):
            if chunk := os.read(key.fd, 8_192):
                key.data.extend(chunk[: _OUTPUT_CAP - len(key.data)])
            else:
                selector.unregister(key.fileobj)
    return True


def _render_output(stdout: bytearray, stderr: bytearray) -> str:
    sections: list[str] = []
    if stdout:
        sections.append(stdout.decode(errors="replace"))
    if stderr:
        sections.append(f"stderr:\n{stderr.decode(errors='replace')}")
    return "\n".join(sections)


def _capped(text: str) -> str:
    return text[:_OUTPUT_CAP]
