"""Default workspace shell tool."""

import os
import signal
import subprocess
from contextlib import suppress
from io import BufferedIOBase
from threading import Thread

from kinby.plugins import ToolContext, tool

_OUTPUT_CAP = 30_000
_JOIN_SECONDS = 2


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
    readers = (
        Thread(target=_read_output, args=(stdout_pipe, stdout), daemon=True),
        Thread(target=_read_output, args=(stderr_pipe, stderr), daemon=True),
    )
    for reader in readers:
        reader.start()
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        for reader in readers:
            reader.join(_JOIN_SECONDS)
        output = _capped(_render_output(stdout, stderr))
        detail = f"\n{output}" if output else ""
        raise TimeoutError(f"Bash timed out after {timeout_seconds} seconds.{detail}") from None
    # A background child can hold the pipes open long after bash exits.
    for reader in readers:
        reader.join(_JOIN_SECONDS)
    output = _render_output(stdout, stderr)
    if return_code:
        output = f"Exit code: {return_code}\n{output}"
    return _capped(output)


def _read_output(stream: BufferedIOBase, output: bytearray) -> None:
    while chunk := stream.read1(8_192):
        remaining = _OUTPUT_CAP - len(output)
        if remaining > 0:
            output.extend(chunk[:remaining])


def _render_output(stdout: bytearray, stderr: bytearray) -> str:
    sections: list[str] = []
    if stdout:
        sections.append(stdout.decode(errors="replace"))
    if stderr:
        sections.append(f"stderr:\n{stderr.decode(errors='replace')}")
    return "\n".join(sections)


def _capped(text: str) -> str:
    return text[:_OUTPUT_CAP]
