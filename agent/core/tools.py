from langchain_core.messages import ToolMessage
import os

from .bash import run_read_only
from .registry import TOOLS, kinby_tool
from .state import KinbyState

MAX_TOOL_CHARS = 8_000


def clip(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    head, tail = limit * 2 // 3, limit // 3
    dropped = len(text) - head - tail
    return (
        text[:head]
        + f"\n\n[... {dropped:,} characters truncated — "
        "narrow the request (grep, head, a smaller path) to see more ...]\n\n"
        + text[-tail:]
    )


@kinby_tool
def read_file(path: str) -> str:
    """Read a UTF-8 text file and return its contents."""
    try:
        return open(path, encoding="utf-8").read()
    except Exception as e:
        return f"ERROR: {e}"


@kinby_tool
def list_files(path: str) -> list[str] | str:
    """List the files in a directory."""
    if os.path.isdir(path):
        return os.listdir(path)
    return "The path is not a directory"


@kinby_tool
def bash(command: str) -> str:
    """Run a read-only shell command. Allowed: ls, cat, type, git status/log/diff, grep, find, python --version, and similar inspect commands. Writes and other programs are blocked."""
    return run_read_only(command)


def run_tools(state: KinbyState):
    messages, files_read = [], []
    for tc in state["messages"][-1].tool_calls:
        name, args = tc["name"], tc["args"]
        tool = TOOLS.get(name)
        result = (
            f"ERROR: unknown tool '{name}'" if tool is None else str(tool.invoke(args))
        )
        messages.append(
            ToolMessage(content=clip(result), tool_call_id=tc["id"], name=name)
        )
        if name == "read_file" and args.get("path"):
            files_read.append(args["path"])
    return {"messages": messages, "files_read": files_read}
