from typing import Literal

from .core.settings import settings
from .core.state import KinbyState
from .core.tools import TOOLS, run_tools
from .core.usage import usage_from_message
from .core.context import needs_compaction, make_compact_node
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
import os

os.environ.setdefault("OPENAI_API_KEY", settings.OPENAI_API_KEY)
os.environ.setdefault("ANTHROPIC_API_KEY", settings.ANTHROPIC_API_KEY)


MODEL = "anthropic:claude-sonnet-5"
llm = init_chat_model(MODEL).bind_tools(list(TOOLS.values()))


def agent(state: KinbyState):
    msgs = state["messages"]
    if state.get("summary"):
        msgs = [
            SystemMessage(
                f"Summary of the conversation so far (reference only):\n{state['summary']}"
            )
        ] + msgs
    message = llm.invoke(msgs)
    usage = usage_from_message(message)
    return {
        "messages": [message],
        "token_usage": usage,
        "last_token_usage": usage,
    }


def route(state: KinbyState) -> Literal["tools", "__end__"]:
    return "tools" if state["messages"][-1].tool_calls else END


g = StateGraph(KinbyState)
g.add_node("agent", agent)
g.add_node("tools", run_tools)
g.add_node("compact", make_compact_node(MODEL))
g.add_conditional_edges(START, needs_compaction)
g.add_conditional_edges("agent", route)
g.add_conditional_edges("tools", needs_compaction)
g.add_edge("compact", "agent")
kinby = g.compile(checkpointer=MemorySaver())
