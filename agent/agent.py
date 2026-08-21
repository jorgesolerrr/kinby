from .core.settings import settings
from .core.state import KinbyState
from .core.tools import TOOLS, run_tools
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
import os

os.environ.setdefault("OPENAI_API_KEY", settings.OPENAI_API_KEY)
os.environ.setdefault("ANTHROPIC_API_KEY", settings.ANTHROPIC_API_KEY)



MODEL = "anthropic:claude-sonnet-5"
llm = init_chat_model(MODEL).bind_tools(list(TOOLS.values()))

def agent(state: KinbyState):
    return {"messages": [llm.invoke(state["messages"])]}

def route(state: KinbyState):
    return "tools" if state["messages"][-1].tool_calls else END

g = StateGraph(KinbyState)
g.add_node("agent", agent); g.add_node("tools", run_tools)
g.add_edge(START, "agent")
g.add_conditional_edges("agent", route)
g.add_edge("tools", "agent")
kinby = g.compile(checkpointer=MemorySaver())