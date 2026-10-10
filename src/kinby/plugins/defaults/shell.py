"""Default workspace shell tool."""

import os
import signal
import subprocess
from contextlib import suppress
from tempfile import TemporaryFile
from typing import BinaryIO

from kinby.plugins import ToolContext, tool

_OUTPUT_CAP = 30_000


@tool(write=True)
def bash(command: str, context: ToolContext) -> str:
    """Run a Bash command in the workspace."""
    timeout_seconds = context.instance.manifest.tools.bash_timeout_seconds
    # Files rather than pipes: a background child the command leaves running can hold a pipe open.
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        process = subprocess.Popen(
            ("bash", "-c", command),
            cwd=context.workspace,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            output = _capped(_render_output(stdout, stderr))
            detail = f"\n{output}" if output else ""
            raise TimeoutError(f"Bash timed out after {timeout_seconds} seconds.{detail}") from None
        output = _render_output(stdout, stderr)
    if return_code:
        output = f"Exit code: {return_code}\n{output}"
    return _capped(output)


def _render_output(stdout: BinaryIO, stderr: BinaryIO) -> str:
    sections: list[str] = []
    if written := _head(stdout):
        sections.append(written.decode(errors="replace"))
    if written := _head(stderr):
        sections.append(f"stderr:\n{written.decode(errors='replace')}")
    return "\n".join(sections)


def _head(output: BinaryIO) -> bytes:
    # pread leaves the offset alone: a background child still writes through the same one.
    return os.pread(output.fileno(), _OUTPUT_CAP, 0)


def _capped(text: str) -> str:
    return text[:_OUTPUT_CAP]
