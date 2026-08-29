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
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    readers = (
        Thread(target=_read_output, args=(process.stdout, stdout)),
        Thread(target=_read_output, args=(process.stderr, stderr)),
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
        output = _render_output(stdout, stderr)
        detail = f"\n{output}" if output else ""
        raise TimeoutError(f"Bash timed out after {_TIMEOUT_SECONDS} seconds.{detail}") from None
    for reader in readers:
        reader.join()
    output = _render_output(stdout, stderr)
    if return_code:
        return f"Exit code: {return_code}\n{output}"[:_OUTPUT_CAP]
    return output


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
    return "\n".join(sections)[:_OUTPUT_CAP]
