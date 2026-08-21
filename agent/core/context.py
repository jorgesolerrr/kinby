from pathlib import Path
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately

from .state import KinbyState

WINDOW = 200_000  # TODO step 5: look this up from the provider string
COMPACT_AT = 0.80  # dsh's DEFAULT_THRESHOLD_RATIO; hermes uses 0.75
KEEP_LAST = 6  # protect-last-N messages
KEEP_TOOL_RESULTS = 3  # newest tool outputs stay verbatim (Anthropic's default keep)

SUMMARY_PROMPT = (
    Path(__file__).parent.parent / "prompts" / "summary.md"
).read_text(encoding="utf-8")


def input_tokens_of_last_call(state: KinbyState) -> int:
    usage = state.get("last_token_usage") or {}
    if usage.get("input_tokens"):
        return int(usage["input_tokens"])
    return count_tokens_approximately(state["messages"])


def needs_compaction(state: KinbyState) -> Literal["compact", "agent"]:
    return (
        "compact"
        if input_tokens_of_last_call(state) > WINDOW * COMPACT_AT
        else "agent"
    )


def prune_tool_results(
    messages: list[BaseMessage], keep: int = KEEP_TOOL_RESULTS
) -> list[ToolMessage]:
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    out = []
    for m in tool_msgs[:-keep] if keep else tool_msgs:
        if str(m.content).startswith("[pruned"):
            continue
        out.append(
            ToolMessage(
                content=(
                    f"[pruned tool result: {len(str(m.content)):,} chars — "
                    "re-run the tool if needed]"
                ),
                tool_call_id=m.tool_call_id,
                id=m.id,
                name=m.name,
            )
        )
    return out


def safe_cut(messages: list[BaseMessage], keep_last: int) -> int:
    """Start of the protected tail. Never cut between an AI tool_call and its ToolMessages."""
    i = max(0, len(messages) - keep_last)
    while i > 0:
        m = messages[i]
        if isinstance(m, HumanMessage) or (
            isinstance(m, AIMessage) and not m.tool_calls
        ):
            break
        i -= 1
    return i


def make_compact_node(model: str):
    summariser = init_chat_model(model)

    def compact(state: KinbyState):
        msgs = state["messages"]
        stubs = prune_tool_results(msgs)
        cut = safe_cut(msgs, KEEP_LAST)
        old = msgs[:cut]
        if not old:
            return {"messages": stubs}
        convo = [SystemMessage(SUMMARY_PROMPT)]
        if state.get("summary"):
            convo.append(HumanMessage(f"Previous summary:\n{state['summary']}"))
        convo += old + [HumanMessage("Write the updated summary now.")]
        summary = summariser.invoke(convo).content
        return {
            "summary": summary,
            "messages": [RemoveMessage(id=m.id) for m in old],
        }

    return compact
