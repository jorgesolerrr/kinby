"""What fills a thread's context size (ADR 0124): each category measured in characters as the
model is sent it, then scaled to add up to the provider's count for the latest model call."""

import json
from collections.abc import Sequence
from itertools import accumulate, pairwise

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from kinby.contracts import ContextCategory, ContextRow, ThreadCompacted
from kinby.core.prompt import PromptSection, PromptSectionName
from kinby.core.sent_history import sent_history
from kinby.core.turn_metrics import SKILL_TOOL
from kinby.plugins.tools import Tool


def context_rows(
    context_size: int,
    sections: Sequence[PromptSection],
    tools: Sequence[Tool],
    mcp_tools: Sequence[Tool],
    messages: Sequence[AnyMessage],
    compaction: ThreadCompacted | None,
    cut: int | None,
) -> list[ContextRow]:
    """The rows of *context_size*: the system prompt's *sections*, the *tools* and the
    *mcp_tools*, and *messages* as they are sent under *compaction* and *cut*."""
    characters = dict.fromkeys(ContextCategory, 0)
    for section in sections:
        characters[_section_category(section.name)] += len(section.text)
    for tool in tools:
        category = ContextCategory.AUTHORED_TOOLS if tool.authored else ContextCategory.CORE_TOOLS
        characters[category] += _tool_characters(tool)
    characters[ContextCategory.MCP_TOOLS] += sum(_tool_characters(tool) for tool in mcp_tools)
    sent = sent_history(messages, compaction, cut)
    if compaction is None:
        del characters[ContextCategory.COMPACTION_SUMMARY]
    else:
        summary, *sent = sent
        characters[ContextCategory.COMPACTION_SUMMARY] = len(summary.text)
    for message in sent:
        characters[_message_category(message)] += _message_characters(message)
    measured = sum(characters.values())
    bounds = [
        round(running * context_size / measured)
        for running in accumulate(characters.values(), initial=0)
    ]
    return [
        ContextRow(category=category, tokens=end - start)
        for category, (start, end) in zip(characters, pairwise(bounds), strict=True)
    ]


def _section_category(name: PromptSectionName) -> ContextCategory:
    match name:
        case PromptSectionName.PROFILE:
            return ContextCategory.PROFILE
        case PromptSectionName.SKILLS:
            return ContextCategory.SKILLS_CATALOGUE
        case _:
            return ContextCategory.SYSTEM_PROMPT


def _tool_characters(tool: Tool) -> int:
    return len(json.dumps(convert_to_openai_tool(tool.runnable)))


def _message_category(message: AnyMessage) -> ContextCategory:
    if not isinstance(message, ToolMessage):
        return ContextCategory.TEXT
    if message.name == SKILL_TOOL:
        return ContextCategory.SKILL_BODIES
    return ContextCategory.TOOL_RESULTS


def _message_characters(message: AnyMessage) -> int:
    calls = message.tool_calls if isinstance(message, AIMessage) else []
    return len(message.text) + sum(
        len(call["name"]) + len(json.dumps(call["args"])) for call in calls
    )
