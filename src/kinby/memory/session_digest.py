"""Digest a Claude Code session into what a factory run's recap reads from it."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from kinby.contracts import CodingSessionId
from kinby.core.coding_clients import claude_tokens
from kinby.jsonl import whole_lines

_FAILED_OUTPUT_TAIL_CHARS = 400
_EXIT_CODE = re.compile(r"Exit code [1-9]\d*")
_MEMORY_SEARCH = ("kinby", "memory", "search")
_SHELL_OPERATORS = frozenset("|&;<>")


@dataclass(frozen=True)
class FailedCommand:
    #: Its first line.
    command: str
    #: The end of what the command printed.
    tail: str


@dataclass(frozen=True)
class MemorySearch:
    """A ``kinby memory search`` the session ran."""

    query: str
    #: What its result added to the session's context, from the transcript's usage. None when the
    #: transcript cannot tell.
    tokens: int | None


@dataclass
class _ModelCall:
    message: str
    input_tokens: int = 0
    output_tokens: int = 0
    #: The characters of each tool result that came back before the next call, by call id.
    results: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionDigest:
    """The path a session took and its prose. Tool outputs and file contents are left out."""

    session: CodingSessionId
    #: Each in the order the session first touched it.
    read: tuple[Path, ...]
    changed: tuple[Path, ...]
    failed: tuple[FailedCommand, ...]
    searches: tuple[MemorySearch, ...]
    said: tuple[str, ...]
    tools: tuple[str, ...]


@dataclass(frozen=True)
class MissingSession:
    """A session whose transcript is no longer in the Claude config directory."""

    session: CodingSessionId


def digest_session(claude_dir: Path, session: CodingSessionId) -> SessionDigest | MissingSession:
    """Digest *session* from its transcript in *claude_dir*, Claude's config directory.

    Claude files a session under a project folder named after its workspace, so the transcript
    is found by its id alone.
    """
    transcript = next(claude_dir.glob(f"projects/*/{session}.jsonl"), None)
    if transcript is None:
        return MissingSession(session)
    read: list[Path] = []
    changed: list[Path] = []
    # The file each Read, Edit and Write call names, and the list it joins once it succeeds.
    files: dict[str, tuple[Path, list[Path]]] = {}
    commands: dict[str, str] = {}
    failed: list[FailedCommand] = []
    # The query of each memory search, by its call id.
    searches: dict[str, str] = {}
    calls: list[_ModelCall] = []
    said: list[str] = []
    tools: list[str] = []
    for line in whole_lines(transcript):
        entry = json.loads(line)
        message = entry.get("message")
        match entry.get("type"), message:
            case "assistant", {"id": str(message_id), "usage": dict(usage)}:
                # Claude records a call in parts, each with the usage counted so far.
                if not calls or calls[-1].message != message_id:
                    calls.append(_ModelCall(message_id))
                counted = claude_tokens(usage)
                calls[-1].input_tokens = counted.input_tokens
                calls[-1].output_tokens = counted.output_tokens
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else ():
            match block:
                case {"type": "tool_result", "tool_use_id": str(call)} if calls:
                    calls[-1].results[call] = len(str(block.get("content")))
            match block:
                case {"type": "text", "text": str(text)} if entry.get("type") == "assistant":
                    said.append(text.strip())
                case {"type": "tool_use", "id": str(call), "name": str(name), "input": dict(args)}:
                    tools.append(name)
                    match name, args:
                        case "Read", {"file_path": str(path)}:
                            files[call] = (Path(path), read)
                        case "Edit" | "Write", {"file_path": str(path)}:
                            files[call] = (Path(path), changed)
                        case "Bash", {"command": str(command)}:
                            # The rest of a command can be a heredoc holding a whole file.
                            commands[call] = next(iter(command.splitlines()), "")
                            if (query := _memory_search_query(command)) is not None:
                                searches[call] = query
                case {
                    "type": "tool_result",
                    "tool_use_id": str(call),
                    "content": str(output),
                    "is_error": True,
                } if call in commands and _EXIT_CODE.match(output):
                    tail = output[-_FAILED_OUTPUT_TAIL_CHARS:].strip()
                    failed.append(FailedCommand(commands[call], tail))
                case {"type": "tool_result", "tool_use_id": str(call)} if (
                    call in files and not block.get("is_error")
                ):
                    path, touched = files[call]
                    touched.append(path)
    tokens = _result_tokens(calls)
    return SessionDigest(
        session=session,
        read=tuple(dict.fromkeys(read)),
        changed=tuple(dict.fromkeys(changed)),
        failed=tuple(failed),
        searches=tuple(MemorySearch(query, tokens.get(call)) for call, query in searches.items()),
        said=tuple(text for text in said if text),
        tools=tuple(dict.fromkeys(tools)),
    )


def _result_tokens(calls: list[_ModelCall]) -> dict[str, int]:
    """Each tool result's tokens by its call id, counted as a turn's memory tokens are: how much
    the next call's input grew, split among one step's results by their characters."""
    tokens: dict[str, int] = {}
    for call, following in pairwise(calls):
        grown = following.input_tokens - call.input_tokens - call.output_tokens
        characters = sum(call.results.values())
        if grown < 0 or not characters:
            continue
        for result, size in call.results.items():
            tokens[result] = round(grown * size / characters)
    return tokens


def _memory_search_query(command: str) -> str | None:
    """The query of the ``kinby memory search`` *command* runs, None when it runs none."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command if " ".join(_MEMORY_SEARCH) in command else None
    width = len(_MEMORY_SEARCH)
    start = next(
        (at + width for at in range(len(words)) if tuple(words[at : at + width]) == _MEMORY_SEARCH),
        None,
    )
    if start is None:
        return None
    query: list[str] = []
    for word in words[start:]:
        if word.startswith("-") or _SHELL_OPERATORS & set(word):
            break
        query.append(word)
    return " ".join(query)
