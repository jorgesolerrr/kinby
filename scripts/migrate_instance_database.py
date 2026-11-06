"""Move each instance's history from its JSONL files into its instance database, once.

    uv run scripts/migrate_instance_database.py <instance directory>...

Run it with the instances stopped. For each one it imports the events, thread records, config
changes and coding-session recalls into a new database in one transaction. It then checks that
every thread has as many events and the same turns as in events.jsonl, and only when every check
passes does it put the database in place and delete the JSONL files. A failed check, or a crash
before the database is in place, leaves the JSONL files as they were. A crash while deleting them
leaves the rest for the next run. kinby never imports it.
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from kinby.contracts import ConfigChange, Event
from kinby.core.runtime_lock import InstanceBusyError, runtime_lock
from kinby.core.threads import ThreadRecord
from kinby.instance import load_instance
from kinby.instance.database import DATABASE_NAME, connect

_EVENTS = "events.jsonl"
_THREADS = "threads.jsonl"
_CONFIG_CHANGES = "config-changes.jsonl"
_RECALLS = "recalls"

#: A thread's event count and its turns, in the order they started.
type Shape = tuple[int, tuple[str, ...]]


class CheckFailed(Exception):
    """A thread reads differently from the database than from its JSONL file."""


def migrate(state_dir: Path) -> str:
    """Move *state_dir*'s JSONL files into its database, and say what moved."""
    old = [
        path
        for path in (state_dir / _EVENTS, state_dir / _THREADS, state_dir / _CONFIG_CHANGES)
        if path.exists()
    ]
    recalls = sorted((state_dir / _RECALLS).glob("*.jsonl"))
    if not old and not recalls:
        return "nothing to migrate"
    database = state_dir / DATABASE_NAME
    # The database goes in place only once its check passed, so one already there was made by a run
    # that stopped while deleting the files.
    if not database.exists():
        partial = state_dir / f"{DATABASE_NAME}.partial"
        for leftover in ("", "-wal", "-shm"):
            Path(f"{partial}{leftover}").unlink(missing_ok=True)
        with closing(connect(partial)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                _import(db, state_dir, recalls)
            _check(db, state_dir / _EVENTS)
        partial.replace(database)
    for path in [*old, *recalls]:
        path.unlink()
    if recalls:
        (state_dir / _RECALLS).rmdir()
    with closing(connect(database)) as db:
        events, threads = db.execute(
            "SELECT count(*), count(DISTINCT thread_id) FROM events"
        ).fetchone()
    return f"migrated {events} events in {threads} threads"


def _import(db: sqlite3.Connection, state_dir: Path, recalls: list[Path]) -> None:
    heads: dict[str, tuple[int, str]] = {}
    for line in _whole_lines(state_dir / _EVENTS):
        event = Event.model_validate_json(line)
        thread_id, at = str(event.thread_id), event.timestamp.isoformat(timespec="microseconds")
        # Keeping the first of two events at one sequence lets the check name the thread.
        db.execute(
            "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                event.sequence,
                str(event.turn_id),
                event.type,
                at,
                _compact(json.loads(line)["payload"]),
            ),
        )
        heads[thread_id] = (event.sequence, at)
    db.executemany(
        "INSERT INTO heads VALUES (?, ?, ?)",
        ((thread_id, sequence, at) for thread_id, (sequence, at) in heads.items()),
    )
    # A rename or an archive appended the thread's record again, and its latest record won.
    threads = {
        thread.id: thread
        for thread in map(ThreadRecord.model_validate_json, _whole_lines(state_dir / _THREADS))
    }
    db.executemany(
        "INSERT INTO threads VALUES (?, ?, ?, ?)",
        (
            (str(thread.id), thread.title, thread.created_at.isoformat(), thread.archived)
            for thread in threads.values()
        ),
    )
    db.executemany(
        "INSERT INTO config_changes VALUES (?, ?)",
        (
            (ConfigChange.model_validate_json(line).file, line)
            for line in _whole_lines(state_dir / _CONFIG_CHANGES)
        ),
    )
    db.executemany(
        "INSERT INTO recalls VALUES (?, ?)",
        ((path.stem, line) for path in recalls for line in _whole_lines(path)),
    )


def _check(db: sqlite3.Connection, events: Path) -> None:
    """Raise unless the database and *events* agree on every thread's shape."""
    before = _shapes((record["thread_id"], record["turn_id"]) for record in _records(events))
    after = _shapes(
        db.execute("SELECT thread_id, turn_id FROM events ORDER BY thread_id, sequence")
    )
    differing = sorted(
        thread for thread in before.keys() | after.keys() if before.get(thread) != after.get(thread)
    )
    if differing:
        raise CheckFailed(
            "".join(
                f"\n  thread {thread}: {_described(after.get(thread))} in the database, "
                f"{_described(before.get(thread))} in {_EVENTS}"
                for thread in differing
            )
        )


def _shapes(events: Iterator[tuple[str, str]]) -> dict[str, Shape]:
    counts: dict[str, int] = {}
    turns: dict[str, dict[str, None]] = {}
    for thread, turn in events:
        counts[thread] = counts.get(thread, 0) + 1
        turns.setdefault(thread, {})[turn] = None
    return {thread: (count, tuple(turns[thread])) for thread, count in counts.items()}


def _described(shape: Shape | None) -> str:
    count, turns = shape or (0, ())
    return f"{count} events in {len(turns)} turns"


def _records(path: Path) -> Iterator[dict[str, str]]:
    for line in _whole_lines(path):
        yield json.loads(line)


def _whole_lines(path: Path) -> Iterator[str]:
    """Each line of *path* that a newline ends. The last line of an append cut short has none, and
    may stop inside a character."""
    if not path.exists():
        return
    with path.open("rb") as lines:
        for line in lines:
            if line.endswith(b"\n"):
                yield line.removesuffix(b"\n").decode()


def _compact(payload: object) -> str:
    """*payload* as JSON, in the form an append writes."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("instances", nargs="+", type=Path, help="instance directories")
    failed = False
    for directory in parser.parse_args().instances:
        state_dir = load_instance(directory).manifest.state_dir
        try:
            with runtime_lock(state_dir):
                print(f"{directory}: {migrate(state_dir)}")
        except (CheckFailed, InstanceBusyError) as exc:
            failed = True
            print(f"{directory}: kept its JSONL files: {exc}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
