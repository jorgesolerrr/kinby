from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
import os
from .state import KinbyState

@tool
def _read_file(path: str) -> str:
    """Read a UTF-8 text file and return its contents."""
    try:
        return open(path, encoding="utf-8").read()
    except Exception as e:
        return f"ERROR: {e}"


@tool
def _list_files(path:str) -> list[str] | str:
    """List the files in a directory"""
    if os.path.isdir(path):
        return os.listdir(path)
    return "The path is not a directory"

TOOLS = {t.name: t for t in [_read_file, _list_files]}

def run_tools(state: KinbyState):
    messages = []
    files_read = []
    for tc in state["messages"][-1].tool_calls:
        name, args = tc["name"], tc["args"]
        messages.append(
            ToolMessage(content=str(TOOLS[name].invoke(args)), tool_call_id=tc["id"])
        )
        if name == "_read_file" and args.get("path"):
            files_read.append(args["path"])
    return {"messages": messages, "files_read": files_read}