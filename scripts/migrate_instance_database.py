"""Move each instance's history from its JSONL files into its instance database, once.

    uv run scripts/migrate_instance_database.py <instance directory>...

Run it with the instances stopped. For each one it imports the events, thread records, config
changes and coding-session recalls into a new database in one transaction. It keeps a delivery's
body only while the delivery waits (ADR 0117): a started delivery keeps only its arrival, and its
turn's message and its code step's call name the delivery instead. It then checks that every thread
has as many events and the same turns as in events.jsonl, and only when every check passes does it
put the database in place, prune each thread's checkpoints, name the delivery in place of its body
in the checkpoints left, and delete the JSONL files. A failed check, or a crash before the database
is in place, leaves the JSONL files as they were. A crash after it leaves the rest for the next
run. An instance already on its database from before ADR 0117 has no JSONL files, and its arrivals
still hold their bodies: it drops them in place, in one transaction, then tidies its checkpoints the
same way. kinby never imports it.
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any  # noqa: TID251 - the JSONL files' boundary
from uuid import UUID

from kinby.contracts import ConfigChange, DeliveryId, Event, EventType, SignalReceived
from kinby.core.runtime_lock import InstanceBusyError, runtime_lock
from kinby.core.scheduler import Delivery
from kinby.core.threads import ThreadRecord
from kinby.core.turn_runner import _CHECKPOINT_SERIALIZER, LangGraphRunner
from kinby.core.turns import TurnRequest
from kinby.instance import Instance, load_instance
from kinby.instance.database import DATABASE_NAME, connect

_EVENTS = "events.jsonl"
_THREADS = "threads.jsonl"
_CONFIG_CHANGES = "config-changes.jsonl"
_RECALLS = "recalls"
_CHECKPOINTS = "checkpoints.sqlite"

#: A thread's event count and its turns, in the order they started.
type Shape = tuple[int, tuple[str, ...]]


class CheckFailed(Exception):
    """A thread reads differently from the database than from its JSONL file."""


def migrate(instance: Instance) -> str:
    """Move *instance*'s JSONL files into its database, and say what moved."""
    state_dir = instance.manifest.state_dir
    old = [
        path
        for path in (state_dir / _EVENTS, state_dir / _THREADS, state_dir / _CONFIG_CHANGES)
        if path.exists()
    ]
    recalls = sorted((state_dir / _RECALLS).glob("*.jsonl"))
    database = state_dir / DATABASE_NAME
    if not old and not recalls and not _holds_bodies(database):
        return "nothing to migrate"
    # The database goes in place only once its check passed, so one already there was made by a run
    # that stopped while deleting the files, or by kinby before ADR 0117.
    if database.exists():
        _drop_bodies(database)
    else:
        partial = state_dir / f"{DATABASE_NAME}.partial"
        for leftover in ("", "-wal", "-shm"):
            Path(f"{partial}{leftover}").unlink(missing_ok=True)
        with closing(connect(partial)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                _import(db, state_dir, recalls)
            _check(db, state_dir / _EVENTS)
        partial.replace(database)
    asyncio.run(_prune_checkpoints(instance))
    _name_deliveries_in_checkpoints(state_dir)
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
    waiting: dict[str, str] = {}
    #: The deliveries whose turns may yet name them, by turn.
    references: dict[str, str] = {}
    for line in _whole_lines(state_dir / _EVENTS):
        record = json.loads(line)
        _drop_body(record, waiting, references)
        event = Event.model_validate(record)
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
                _compact(record["payload"]),
            ),
        )
        heads[thread_id] = (event.sequence, at)
    db.executemany(
        "INSERT INTO heads VALUES (?, ?, ?)",
        ((thread_id, sequence, at) for thread_id, (sequence, at) in heads.items()),
    )
    db.executemany("INSERT INTO waiting_deliveries VALUES (?, ?)", waiting.items())
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


def _drop_body(record: dict[str, Any], waiting: dict[str, str], references: dict[str, str]) -> None:
    """Keep a delivery's arrival in *record*, and its body in *waiting* until its turn starts. A
    started turn's message and its code step's call name the delivery instead (ADR 0117)."""
    payload, turn = record["payload"], record["turn_id"]
    match payload["type"]:
        case EventType.SIGNAL_RECEIVED if "body" in payload["delivery"]:
            old = payload["delivery"]
            arrival = Delivery(
                headers=old["headers"],
                content_type=old["content_type"],
                body=old["body"],
                delivery_id=DeliveryId(old["delivery_id"]) if old.get("delivery_id") else None,
                received_at=datetime.fromisoformat(old["received_at"]),
            ).arrival()
            payload["delivery"] = arrival.model_dump(mode="json")
            waiting[turn] = old["body"]
            references[turn] = arrival.reference
        case EventType.TURN_STARTED if turn in waiting:
            del waiting[turn]
            payload["message"] = references[turn]
        case EventType.TOOL_CALL if turn in references:
            signal = payload["arguments"].get("signal")
            # A code step's call comes first in its turn, and carries the whole delivery.
            if isinstance(signal, dict) and "body" in signal:
                payload["arguments"]["signal"] = references[turn]
            del references[turn]


def _holds_bodies(database: Path) -> bool:
    """Whether *database* keeps a delivery's body in its arrival, as kinby did before ADR 0117."""
    if not database.exists():
        return False
    with closing(connect(database)) as db:
        return bool(
            db.execute(
                "SELECT 1 FROM events WHERE type = ?"
                " AND json_extract(payload, '$.delivery.body') IS NOT NULL LIMIT 1",
                (EventType.SIGNAL_RECEIVED,),
            ).fetchone()
        )


def _drop_bodies(database: Path) -> None:
    """Rewrite the arrivals, turn messages and code step calls that still hold a delivery's body,
    as the import would have written them."""
    waiting: dict[str, str] = {}
    references: dict[str, str] = {}
    with closing(connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT rowid, turn_id, payload FROM events WHERE type IN (?, ?, ?) ORDER BY rowid",
            (EventType.SIGNAL_RECEIVED, EventType.TURN_STARTED, EventType.TOOL_CALL),
        ).fetchall()
        for rowid, turn_id, payload in rows:
            record = {"turn_id": turn_id, "payload": json.loads(payload)}
            _drop_body(record, waiting, references)
            if (rewritten := _compact(record["payload"])) != payload:
                db.execute("UPDATE events SET payload = ? WHERE rowid = ?", (rewritten, rowid))
        db.executemany("INSERT OR IGNORE INTO waiting_deliveries VALUES (?, ?)", waiting.items())


async def _prune_checkpoints(instance: Instance) -> None:
    """Keep each thread's newest completed checkpoint, and any parked after it."""
    if not (instance.manifest.state_dir / _CHECKPOINTS).exists():
        return
    runner = LangGraphRunner(instance)
    with closing(connect(instance.manifest.state_dir / DATABASE_NAME)) as db:
        threads = [UUID(thread_id) for (thread_id,) in db.execute("SELECT thread_id FROM heads")]
    for thread_id in threads:
        await runner.prune_checkpoints(thread_id)


def _name_deliveries_in_checkpoints(state_dir: Path) -> None:
    """A checkpoint keeps the turn it ran, whose message was its delivery's body. Name the
    delivery there too, rewriting each checkpoint and write in place, so a parked turn still
    resumes from it."""
    checkpoints = state_dir / _CHECKPOINTS
    if not checkpoints.exists():
        return
    with closing(connect(state_dir / DATABASE_NAME)) as db:
        references = {
            UUID(turn_id): SignalReceived.model_validate_json(payload).delivery.reference
            for turn_id, payload in db.execute(
                "SELECT turn_id, payload FROM events WHERE type = ?", (EventType.SIGNAL_RECEIVED,)
            )
        }

    def named(turn: object) -> TurnRequest | None:
        if not isinstance(turn, TurnRequest) or turn.turn_id not in references:
            return None
        reference = references[turn.turn_id]
        return None if turn.message == reference else replace(turn, message=reference)

    with closing(sqlite3.connect(checkpoints)) as db, db:
        for rowid, type_, blob in db.execute("SELECT rowid, type, checkpoint FROM checkpoints"):
            checkpoint = _CHECKPOINT_SERIALIZER.loads_typed((type_, blob))
            turn = named(checkpoint["channel_values"].get("turn"))
            if turn is not None:
                checkpoint["channel_values"]["turn"] = turn
                db.execute(
                    "UPDATE checkpoints SET type = ?, checkpoint = ? WHERE rowid = ?",
                    (*_CHECKPOINT_SERIALIZER.dumps_typed(checkpoint), rowid),
                )
        writes = db.execute("SELECT rowid, type, value FROM writes WHERE channel = 'turn'")
        for rowid, type_, blob in writes.fetchall():
            turn = named(_CHECKPOINT_SERIALIZER.loads_typed((type_, blob)))
            if turn is not None:
                db.execute(
                    "UPDATE writes SET type = ?, value = ? WHERE rowid = ?",
                    (*_CHECKPOINT_SERIALIZER.dumps_typed(turn), rowid),
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
        instance = load_instance(directory)
        try:
            with runtime_lock(instance.manifest.state_dir):
                print(f"{directory}: {migrate(instance)}")
        except (CheckFailed, InstanceBusyError) as exc:
            failed = True
            print(f"{directory}: kept its JSONL files: {exc}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
