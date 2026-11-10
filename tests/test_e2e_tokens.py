"""The token probe the e2e pass runs on an instance, `.claude/skills/e2e-pass/tokens.py`."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    ErrorCode,
    Event,
    ModelCompleted,
    Payload,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
)
from tests.helpers import write_events

PROBE_SCRIPT = Path(__file__).parents[1] / ".claude" / "skills" / "e2e-pass" / "tokens.py"
spec = importlib.util.spec_from_file_location("e2e_tokens", PROBE_SCRIPT)
assert spec is not None and spec.loader is not None
e2e_tokens = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e_tokens)

THREAD = UUID("aaaaaaaa-0000-4000-8000-000000000000")
OTHER_THREAD = UUID("bbbbbbbb-0000-4000-8000-000000000000")
STARTED = datetime(2026, 11, 1, 10, tzinfo=UTC)
SOURCE_READ = "sed -n 1,80p /usr/local/lib/python3.14/site-packages/kinby/core/turns.py"
REFUSAL = "INVALID_PARAMS: factory.get has no param 'id'."
TURNS_HEADER = [
    *("started", "thread", "calls", "input", "cache", "read", "cache", "write"),
    *("cache", "write", "after", "first", "call"),
]
RESULTS_HEADER = [
    "tool",
    "results",
    "chars",
    "largest",
    "resent",
    "in",
    "turn",
    "resent",
    "later",
    "errors",
]


def _call(input_tokens: int, cache_read: int = 0, cache_write: int = 0) -> ModelCompleted:
    return ModelCompleted(
        model="claude-sonnet-5",
        duration_ms=1,
        input_tokens=input_tokens,
        output_tokens=10,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_write,
    )


def _bash(call_id: str, command: str) -> ToolCall:
    return ToolCall(call_id=call_id, name="bash", arguments={"command": command})


def _turn(thread_id: UUID, minute: int, *payloads: Payload) -> list[Event]:
    turn_id = uuid4()
    at = STARTED + timedelta(minutes=minute)
    return [
        Event(sequence=0, thread_id=thread_id, turn_id=turn_id, timestamp=at, payload=payload)
        for payload in payloads
    ]


def _instance(directory: Path) -> Path:
    """An instance whose home thread read stats, the installed source and a refused factory, then
    spoke twice more, and a second thread of one call."""
    history = [
        *_turn(
            THREAD,
            0,
            _call(1000, cache_write=800),
            _bash("stats", "kinby hub read stats.get | jq .instances"),
            _bash("source", SOURCE_READ),
            ToolResult(call_id="stats", name="bash", output="s" * 5000, error=False),
            ToolResult(call_id="source", name="bash", output="b" * 300, error=False),
            _call(7000, cache_read=1000, cache_write=6000),
            _bash("factory", "kinby hub read factory.get id=course 2>&1"),
            ToolResult(call_id="factory", name="bash", output=REFUSAL, error=True),
            _call(7100, cache_read=7000, cache_write=100),
            TurnCompleted(input_tokens=15100, output_tokens=30),
            # The memory recap's call, after the turn closed.
            _call(500, cache_write=500),
        ),
        *_turn(THREAD, 5, _call(9000, cache_read=8000), _call(9100, cache_read=9000)),
        *_turn(OTHER_THREAD, 9, _call(2000, cache_write=2000)),
    ]
    return _write(directory, history)


def _write(directory: Path, history: list[Event]) -> Path:
    sequences: dict[UUID, int] = {}
    records = []
    for event in history:
        sequences[event.thread_id] = sequences.get(event.thread_id, 0) + 1
        record = event.model_dump(mode="json")
        record["sequence"] = sequences[event.thread_id]
        record["timestamp"] = event.timestamp.isoformat(timespec="microseconds")
        records.append(record)
    write_events(directory / ".state", records)
    return directory


def _section(out: str, title: str) -> list[list[str]]:
    """The rows of the section under *title*, each split into its words."""
    lines = out.split(f"{title}\n", 1)[1].split("\n\n", 1)[0].splitlines()
    return [line.split() for line in lines]


def test_the_probe_prints_each_turns_tokens_and_model_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert e2e_tokens.main([str(_instance(tmp_path))]) == 0

    assert _section(capsys.readouterr().out, "Turns") == [
        TURNS_HEADER,
        ["2026-11-01", "10:00:00", "aaaaaaaa", "4", "15,600", "8,000", "7,400", "6,100"],
        ["2026-11-01", "10:05:00", "aaaaaaaa", "2", "18,100", "17,000", "0", "0"],
        ["2026-11-01", "10:09:00", "bbbbbbbb", "1", "2,000", "0", "2,000", "0"],
        ["total", "7", "35,700", "25,000", "9,400", "6,100"],
    ]


def test_the_probe_prints_result_sizes_resent_characters_and_errors_by_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert e2e_tokens.main([str(_instance(tmp_path))]) == 0

    refusal = f"{len(REFUSAL):,}"
    assert _section(capsys.readouterr().out, "Tool results") == [
        RESULTS_HEADER,
        ["bash", "stats.get", "1", "5,000", "5,000", "5,000", "10,000", "0"],
        ["bash", "1", "300", "300", "300", "600", "0"],
        ["bash", "factory.get", "1", refusal, refusal, "0", f"{2 * len(REFUSAL):,}", "1"],
    ]


def test_the_probe_names_every_hub_method_a_bash_command_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    reads = (
        "kinby hub read --table instance.list; "
        """kinby hub read --params '{"instance_id": "a"}' 'thread.list' | head; """
        'stats="$(kinby hub read stats.get)"; logs=`kinby hub read instance.logs`; '
        "for m in a b; do kinby hub read $m; done"
    )
    history = _turn(
        THREAD,
        0,
        _call(1000),
        _bash("reads", reads),
        ToolResult(call_id="reads", name="bash", output="r" * 40, error=False),
        _call(1100),
    )

    assert e2e_tokens.main([str(_write(tmp_path, history))]) == 0

    assert _section(capsys.readouterr().out, "Tool results") == [
        RESULTS_HEADER,
        [
            "bash",
            "instance.list,thread.list,stats.get,instance.logs",
            "1",
            "40",
            "40",
            "0",
            "0",
            "0",
        ],
    ]


def test_a_failed_turns_results_are_not_resent_on_later_turns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    history = [
        *_turn(
            THREAD,
            0,
            _call(1000),
            ToolResult(call_id="ls", name="bash", output="b" * 300, error=False),
            _call(2000),
            TurnFailed(code=ErrorCode.INTERNAL, message="boom"),
        ),
        *_turn(THREAD, 5, _call(900), _call(950)),
    ]

    assert e2e_tokens.main([str(_write(tmp_path, history))]) == 0

    assert _section(capsys.readouterr().out, "Tool results") == [
        RESULTS_HEADER,
        ["bash", "1", "300", "300", "0", "0", "0"],
    ]


def test_the_probe_prints_the_shell_commands_that_read_kinbys_installed_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert e2e_tokens.main([str(_instance(tmp_path))]) == 0

    assert _section(capsys.readouterr().out, "Source reads") == [
        ["2026-11-01", "10:00:00", "aaaaaaaa", *SOURCE_READ.split()]
    ]


def test_the_probe_reads_only_the_turns_since_a_time(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert e2e_tokens.main([str(_instance(tmp_path)), "--since", "2026-11-01T10:05"]) == 0

    out = capsys.readouterr().out
    assert _section(out, "Turns")[-1] == ["total", "3", "20,100", "17,000", "2,000", "0"]
    assert _section(out, "Tool results") == [["none"]]
    assert _section(out, "Source reads") == [["none"]]


def test_the_probe_writes_nothing_to_the_instance(tmp_path: Path):
    instance = _instance(tmp_path)
    before = {path: path.read_bytes() for path in instance.rglob("*") if path.is_file()}

    assert e2e_tokens.main([str(instance)]) == 0

    assert {path: path.read_bytes() for path in instance.rglob("*") if path.is_file()} == before
