from collections.abc import Callable

from langchain_core.tools import BaseTool, tool

TOOLS: dict[str, BaseTool] = {}


def kinby_tool(fn: Callable | None = None, **kwargs):
    """Decorate a function as a LangChain tool and register it for the agent."""

    def wrap(f: Callable):
        t = tool(**kwargs)(f) if kwargs else tool(f)
        TOOLS[t.name] = t
        return t

    if fn is not None:
        return wrap(fn)
    return wrap
