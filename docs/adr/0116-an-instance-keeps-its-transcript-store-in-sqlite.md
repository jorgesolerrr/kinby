# An instance keeps its transcript store in SQLite

Supersedes [ADR 0005](0005-event-log-persists-as-jsonl.md).

One append-only `events.jsonl` made every read cost the whole history: returning one thread parsed every event of every thread, and the scheduler replayed the log about five times a tick. An instance now keeps its **events**, its thread records and its waiting **deliveries** in one kinby-owned SQLite database in its state directory, `instance.sqlite`, in WAL mode. Events are indexed by thread and sequence, by turn, and by type and time. A question reads only the events it needs: one thread or turn reads that thread or turn, and a question about every thread asks for the event types it folds. A projection table is added only where a measurement shows a query too slow; the per-thread head is the one kept from the start. The store's API stays synchronous and runs on the event loop, because an indexed read takes well under a millisecond. Heavy readers such as stats over a long range, reflection and the learning review run in a worker thread on their own connection.

ADR 0006 holds: one process owns appends and live delivery. ADR 0072 holds: events are never rewritten or deleted, with one exception. The migration from `events.jsonl` may drop copies of a delivery's body that the new store no longer records (ADR 0117), as long as replaying any thread shows the same turns.

## Considered options

- **An offsets index beside the JSONL.** Rejected. It keeps two files consistent by hand, and questions about every thread still scan.
- **One JSONL file per thread.** Rejected. It fixes reading one thread, but questions about every thread then scan thousands of files.
- **Projection tables for every derived state.** Rejected. Each rule would live twice, once in the projection updater and once in the fold that rebuilds it.

## Consequences

- kinby keeps no JSONL stores of its own. Thread records, config changes and a coding session's recalls move into `instance.sqlite` too, and `kinby/jsonl.py` goes away. Claude Code's session files stay in Claude Code's format.
- The move is a clean cut. A one-off script migrates each instance once, with the instance stopped, and is deleted afterwards. kinby has no code that imports or falls back to JSONL. An instance whose state directory was never migrated refuses to start.
- Reading a transcript takes `sqlite3` or `kinby thread show`, which prints a thread's events as JSON. The playground host has no `sqlite3` binary.
- LangGraph's `checkpoints.sqlite` stays a separate file that LangGraph owns.
