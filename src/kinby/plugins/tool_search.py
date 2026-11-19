"""Tool search: the three tools a turn finds its searchable tools with, reads one's definition
with, and calls one by name with (ADR 0129). The turn runner makes a call_tool call as a call to
the tool it names, so call_tool itself never runs."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace

from langchain_core.utils.function_calling import convert_to_openai_function
from pydantic import JsonValue

from kinby.plugins.registry import ToolSnapshot
from kinby.plugins.tools import Tool, tool

CALL_TOOL = "call_tool"
_WORD = re.compile(r"[a-z0-9]+")
#: A character a Python name cannot hold: any but a letter, a digit or `_`, or a leading digit.
_NOT_IN_A_NAME = re.compile(r"^\d|\W")
#: BM25's term saturation and length normalisation, at their usual values.
_K1 = 1.2
_B = 0.75
_PYTHON_TYPES = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
    "null": "None",
}


@dataclass(frozen=True)
class SearchableGroup:
    """A tool group's searchable tools on a turn, and what the system prompt says of the group."""

    name: str
    line: str | None
    tools: tuple[Tool, ...]


def tool_search(searchable: ToolSnapshot, reachable: ToolSnapshot) -> tuple[Tool, ...]:
    """search_tools, which ranks the turn's *searchable* tools, and describe_tool and call_tool,
    which take any of its *reachable* tools: the loaded ones and the searchable ones. Their
    definitions are the same whatever the tools are, so the prompt cache holds."""

    @tool(write=False)
    def search_tools(query: str, limit: int = 5) -> str:
        """Search the tools you were not sent by what they do, for names, write flags and uses."""
        found = _ranked(searchable.tools, query)[:limit]
        if not found:
            return f'No searchable tool matches "{query}".'
        return "\n".join(
            f"{each.name} ({'writes' if each.write else 'reads'}): {_first_line(each)}"
            for each in found
        )

    @tool(write=False)
    def describe_tool(name: str) -> str:
        """Read a tool's full description, its arguments' schema and its Python signature."""
        described = reachable.get(name)
        if described is None:
            return _unknown(name)
        return _description(described, reachable)

    @tool(write=False)
    def call_tool(name: str, arguments: dict[str, JsonValue]) -> str:
        """Call a tool by name with its arguments, as describe_tool gives them."""
        raise AssertionError("The turn runner makes a call_tool call as the tool it names.")

    return (
        search_tools,
        describe_tool,
        replace(call_tool, check=lambda arguments: _refusal(called(arguments, reachable))),
    )


def called(
    arguments: Mapping[str, JsonValue], reachable: ToolSnapshot
) -> tuple[Tool, dict[str, JsonValue]] | str:
    """The tool of *reachable* that a call_tool call with *arguments* names, and the arguments
    it passes on, or why it names none."""
    name = arguments.get("name")
    passed = arguments.get("arguments", {})
    named = reachable.get(name) if isinstance(name, str) else None
    if named is None:
        return _unknown(str(name))
    if not isinstance(passed, dict):
        return f'call_tool takes the arguments of "{named.name}" as an object.'
    return named, passed


def wrong_arguments(target: Tool, arguments: Mapping[str, JsonValue]) -> str | None:
    """Each field of *arguments* that *target*'s schema refuses, or None when it takes them."""
    # jsonschema loads only in a turn that calls a tool through tool search.
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(_input_schema(target)).iter_errors(arguments),
        key=lambda error: list(map(str, error.absolute_path)),
    )
    if not errors:
        return None
    fields = "\n".join(
        f"- {'.'.join(map(str, error.absolute_path)) or 'arguments'}: {error.message}"
        for error in errors
    )
    return f'Tool "{target.name}" was not called, as its arguments are wrong:\n{fields}'


def python_name(name: str) -> str:
    """The name a script calls the tool *name* by."""
    return _NOT_IN_A_NAME.sub("_", name)


def _unknown(name: str) -> str:
    return f'No tool "{name}" is loaded or searchable in this turn. Find one with search_tools.'


def _refusal(found: tuple[Tool, dict[str, JsonValue]] | str) -> str | None:
    return found if isinstance(found, str) else None


def _ranked(tools: tuple[Tool, ...], query: str) -> list[Tool]:
    """*tools* that hold a word of *query* in their name or description, best match first, by
    BM25."""
    documents = [Counter(_words(f"{each.name} {each.runnable.description}")) for each in tools]
    if not documents:
        return []
    average = sum(document.total() for document in documents) / len(documents)
    holding = Counter(word for document in documents for word in document)
    scored = []
    for each, document in zip(tools, documents, strict=True):
        length = _B * document.total() / average + 1 - _B
        score = sum(
            math.log(1 + (len(documents) - holding[word] + 0.5) / (holding[word] + 0.5))
            * document[word]
            * (_K1 + 1)
            / (document[word] + _K1 * length)
            for word in set(_words(query))
            if document[word]
        )
        if score > 0:
            scored.append((score, each))
    return [each for _, each in sorted(scored, key=lambda pair: -pair[0])]


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _first_line(described: Tool) -> str:
    return described.runnable.description.strip().partition("\n")[0]


def _input_schema(described: Tool) -> dict[str, JsonValue]:
    """The tool's arguments' JSON schema, as the model is sent it."""
    return convert_to_openai_function(described.runnable)["parameters"]


def _description(described: Tool, reachable: ToolSnapshot) -> str:
    sections = [
        f"{described.name} ({'writes' if described.write else 'reads'})",
        described.runnable.description.strip(),
        f"Input schema:\n{json.dumps(_input_schema(described))}",
    ]
    if described.output_schema is not None:
        sections.append(f"Output schema:\n{json.dumps(described.output_schema)}")
    named = python_name(described.name)
    clashing = [
        each.name
        for each in reachable.tools
        if each.name != described.name and python_name(each.name) == named
    ]
    if clashing:
        sections.append(
            f"Scripts leave this tool out: {', '.join(clashing)} also takes the Python name "
            f"{named}."
        )
    else:
        sections.append(f"Python signature:\n{_signature(named, described)}")
    return "\n\n".join(sections)


def _signature(named: str, described: Tool) -> str:
    """How a script calls *described*: by keyword, required arguments first."""
    schema = _input_schema(described)
    properties = schema.get("properties")
    required = schema.get("required")
    fields = properties if isinstance(properties, dict) else {}
    needed = required if isinstance(required, list) else []
    parameters = [
        f"{python_name(field)}: {_python_type(value)}"
        for field, value in fields.items()
        if field in needed
    ] + [
        f"{python_name(field)}: {_python_type(value)} = "
        f"{repr(value.get('default')) if isinstance(value, dict) else 'None'}"
        for field, value in fields.items()
        if field not in needed
    ]
    keywords = f"*, {', '.join(parameters)}" if parameters else ""
    returned = "dict" if described.output_schema is not None else "Any"
    return f"async def {named}({keywords}) -> {returned}"


def _python_type(schema: JsonValue) -> str:
    if not isinstance(schema, dict):
        return "Any"
    options = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(options, list):
        return " | ".join(dict.fromkeys(map(_python_type, options)))
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(_PYTHON_TYPES.get(str(each), "Any") for each in kind)
    return _PYTHON_TYPES.get(str(kind), "Any")
