"""Print what an instance spent on tokens, from its events.

    tokens.py <instance-dir> [--since WHEN]

- Turns: each turn's model calls, its input tokens, the cache reads and writes among them, and
  the cache writes after its first call, which show when the turn lost its cache.
- Tool results: by tool, and by method for a bash command that runs `kinby hub read`. The
  characters the results returned, the characters sent again on later model calls of their turn
  and on the model calls of later turns of their thread, and the errors. A result is first sent
  on the model call after it, and every call after that one sends it again. Only a completed
  turn's results reach later turns: a failed or interrupted turn stays out of the thread's
  history.
- Source reads: the bash commands that name kinby's installed package.

It opens the instance's database read-only and writes nothing to the instance.
"""

import argparse
import re
import shlex
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from kinby.contracts import (
    Event,
    EventType,
    ModelCompleted,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
)
from kinby.core.events import EventReader
from kinby.instance.database import DATABASE_NAME
from kinby.instance.layout import STATE_DIR

READ = {
    EventType.MODEL_COMPLETED,
    EventType.TOOL_CALL,
    EventType.TOOL_RESULT,
    EventType.TURN_COMPLETED,
    EventType.TURN_FAILED,
    EventType.TURN_INTERRUPTED,
}
#: In a path under site-packages or dist-packages.
INSTALLED_PACKAGE = "-packages/kinby"
COMMAND_WIDTH = 160
METHOD = re.compile(r"[\w.]+")
SUBSTITUTION = re.compile(r"\$\(|`")
#: What a substitution's start becomes: a separator, then a mark that stays in a quoted word.
SUBSTITUTED = " ;\0 "
SHELLS = {"bash", "sh", "zsh"}
#: The option that makes a shell run its next argument as a command: `-c`, `-lc`, `-ec`.
SHELL_COMMAND = re.compile(r"-[a-z]*c")
#: The words after which a command starts. A newline becomes `;` before the split.
COMMAND_STARTS = {";", "&&", "||", "|", "&", "(", "\0", "do", "then", "else", "{", "!"}


@dataclass(eq=False)
class Turn:
    started: datetime
    thread_id: UUID
    calls: list[ModelCompleted] = field(default_factory=list)
    #: The calls before the turn closed. A memory recap's call comes after.
    loop_calls: int = 0
    closed: bool = False
    #: Whether it ran to its end, which keeps its results in the thread's history.
    completed: bool = False
    #: The loop calls of the later turns of its thread.
    later_calls: int = 0


@dataclass(frozen=True)
class Result:
    turn: Turn
    tool: str
    chars: int
    error: bool
    calls_before: int

    @property
    def resent_in_turn(self) -> int:
        return self.chars * max(0, self.turn.loop_calls - self.calls_before - 1)

    @property
    def resent_later(self) -> int:
        return self.chars * self.turn.later_calls if self.turn.completed else 0


@dataclass(frozen=True)
class SourceRead:
    at: datetime
    thread_id: UUID
    command: str


@dataclass(frozen=True)
class Spend:
    turns: list[Turn]
    results: list[Result]
    source_reads: list[SourceRead]


def spend(events: Iterable[Event]) -> Spend:
    turns: dict[UUID, Turn] = {}
    methods: dict[str, str] = {}
    results: list[Result] = []
    source_reads: list[SourceRead] = []
    for event in events:
        turn = turns.setdefault(event.turn_id, Turn(event.timestamp, event.thread_id))
        match event.payload:
            case ModelCompleted() as completed:
                turn.calls.append(completed)
                turn.loop_calls += not turn.closed
            case ToolCall(call_id=call_id, name="bash", arguments={"command": str(command)}):
                if INSTALLED_PACKAGE in command:
                    source_reads.append(SourceRead(event.timestamp, event.thread_id, command))
                if read := _hub_reads(command):
                    methods[call_id] = ",".join(dict.fromkeys(read))
            case ToolResult() as result:
                tool = result.name
                if result.call_id in methods:
                    tool = f"{result.name} {methods[result.call_id]}"
                results.append(
                    Result(turn, tool, len(result.output), result.error, turn.loop_calls)
                )
            case TurnCompleted():
                turn.closed = turn.completed = True
            case TurnFailed() | TurnInterrupted():
                turn.closed = True
    remaining: defaultdict[UUID, int] = defaultdict(int)
    for turn in turns.values():
        remaining[turn.thread_id] += turn.loop_calls
    for turn in turns.values():
        remaining[turn.thread_id] -= turn.loop_calls
        turn.later_calls = remaining[turn.thread_id]
    return Spend(list(turns.values()), results, source_reads)


def _hub_reads(command: str) -> list[str]:
    """The methods of the `kinby hub read` commands in *command*, inside command substitutions
    and `bash -c` too: the first argument of each that is not an option or the value of
    `--params`."""
    lexer = shlex.shlex(
        SUBSTITUTION.sub(SUBSTITUTED, command.replace("\n", " ; ")),
        posix=True,
        punctuation_chars=True,
    )
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return []
    methods = []
    for start, word in enumerate(words):
        # A substitution inside quotes, as in "$(kinby hub read …)", stays in one word, and so
        # does the command a shell runs, as in bash -c 'kinby hub read …'.
        if ("\0" in word and word != "\0") or _shell_command(words[:start]):
            methods += _hub_reads(word)
        if words[start : start + 3] != ["kinby", "hub", "read"]:
            continue
        arguments = iter(words[start + 3 :])
        for word in arguments:
            if word == "--params":
                next(arguments, None)
            elif not word.startswith("-"):
                if METHOD.fullmatch(word):
                    methods.append(word)
                break
    return methods


def _shell_command(before: list[str]) -> bool:
    """Whether the word after *before* is the command of a shell's `-c`, the shell starting a
    command rather than being printed, as in `echo bash -c '…'`."""
    return (
        len(before) >= 2
        and SHELL_COMMAND.fullmatch(before[-1]) is not None
        and before[-2].rsplit("/", 1)[-1] in SHELLS
        and (len(before) == 2 or before[-3] in COMMAND_STARTS)
    )


def _cell(value: str | int) -> str:
    return f"{value:,}" if isinstance(value, int) else value


def _table(header: Sequence[str], rows: Iterable[Sequence[str | int]]) -> str:
    """The rows under *header*, the first column aligned left and the others right."""
    cells = [list(header), *([_cell(value) for value in row] for row in rows)]
    widths = [max(len(row[column]) for row in cells) for column in range(len(header))]
    return "\n".join(
        "  ".join(
            value.ljust(width) if column == 0 else value.rjust(width)
            for column, (value, width) in enumerate(zip(row, widths, strict=True))
        )
        for row in cells
    )


def _when(at: datetime) -> str:
    return f"{at:%Y-%m-%d %H:%M:%S}"


def _usage(calls: list[ModelCompleted]) -> tuple[int, int, int, int]:
    return (
        len(calls),
        sum(call.input_tokens for call in calls),
        sum(call.cache_read_tokens for call in calls),
        sum(call.cache_creation_tokens for call in calls),
    )


def _later_writes(turn: Turn) -> int:
    return sum(call.cache_creation_tokens for call in turn.calls[1 : turn.loop_calls])


def _turns(turns: list[Turn]) -> str:
    rows = [
        (_when(turn.started), str(turn.thread_id)[:8], *_usage(turn.calls), _later_writes(turn))
        for turn in turns
    ]
    total = (
        "total",
        "",
        *_usage([call for turn in turns for call in turn.calls]),
        sum(_later_writes(turn) for turn in turns),
    )
    header = (
        "started",
        "thread",
        "calls",
        "input",
        "cache read",
        "cache write",
        "cache write after first call",
    )
    return _table(header, [*rows, total])


def _results(results: list[Result]) -> str:
    by_tool: defaultdict[str, list[Result]] = defaultdict(list)
    for result in results:
        by_tool[result.tool].append(result)
    if not by_tool:
        return "none"
    rows = sorted(
        (
            (
                tool,
                len(found),
                sum(result.chars for result in found),
                max(result.chars for result in found),
                sum(result.resent_in_turn for result in found),
                sum(result.resent_later for result in found),
                sum(result.error for result in found),
            )
            for tool, found in by_tool.items()
        ),
        key=lambda row: row[2],
        reverse=True,
    )
    header = ("tool", "results", "chars", "largest", "resent in turn", "resent later", "errors")
    return _table(header, rows)


def _source_reads(reads: list[SourceRead]) -> str:
    if not reads:
        return "none"
    return "\n".join(
        f"{_when(read.at)}  {str(read.thread_id)[:8]}  "
        f"{read.command.splitlines()[0][:COMMAND_WIDTH]}"
        for read in reads
    )


def report(found: Spend) -> str:
    return (
        f"Turns\n{_turns(found.turns)}\n\n"
        f"Tool results\n{_results(found.results)}\n\n"
        f"Source reads\n{_source_reads(found.source_reads)}"
    )


def read_events(instance: Path, since: datetime | None) -> list[Event]:
    database = (instance / STATE_DIR / DATABASE_NAME).resolve()
    if not database.is_file():
        raise SystemExit(f"{database} does not exist.")
    # A reader creates the -wal and -shm files of a database no connection holds open. Nothing
    # writes to such a database, so it is read as immutable.
    wal = database.with_name(f"{DATABASE_NAME}-wal")
    mode = "ro" if wal.exists() else "ro&immutable=1"
    with closing(sqlite3.connect(f"{database.as_uri()}?mode={mode}", uri=True)) as db:
        return list(EventReader(db).events_of(READ, since=since))


def moment(text: str) -> datetime:
    """An ISO date or time, in UTC when it names no zone."""
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tokens.py", description=__doc__.splitlines()[0])
    parser.add_argument("instance", type=Path, help="the instance's directory")
    parser.add_argument("--since", type=moment, help="only events from this ISO date or time")
    args = parser.parse_args(argv)
    print(report(spend(read_events(args.instance, args.since))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
