"""Default workspace shell tool."""

import subprocess
from threading import Thread
from typing import BinaryIO

from kinby.plugins import ToolContext, tool

_TIMEOUT_SECONDS = 120
_OUTPUT_CAP = 30_000


@tool(write=True)
def bash(command: str, context: ToolContext) -> str:
    """Run a Bash command in the workspace."""
    process = subprocess.Popen(
        ("bash", "-c", command),
        cwd=context.workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_pipe = process.stdout
    stderr_pipe = process.stderr
    if stdout_pipe is None or stderr_pipe is None:
        raise RuntimeError("Bash pipes were not opened.")
    stdout = bytearray()
    stderr = bytearray()
    readers = (
        Thread(target=_read_output, args=(stdout_pipe, stdout)),
        Thread(target=_read_output, args=(stderr_pipe, stderr)),
    )
    for reader in readers:
        reader.start()
    try:
        return_code = process.wait(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join()
        output = _capped(_render_output(stdout, stderr))
        detail = f"\n{output}" if output else ""
        raise TimeoutError(f"Bash timed out after {_TIMEOUT_SECONDS} seconds.{detail}") from None
    for reader in readers:
        reader.join()
    output = _render_output(stdout, stderr)
    if return_code:
        output = f"Exit code: {return_code}\n{output}"
    return _capped(output)


def _read_output(stream: BinaryIO, output: bytearray) -> None:
    while chunk := stream.read(8_192):
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
