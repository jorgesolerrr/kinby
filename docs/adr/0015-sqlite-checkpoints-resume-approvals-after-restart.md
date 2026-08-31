# SQLite checkpoints resume approvals after restart

`LangGraphRunner` persists graph state with `AsyncSqliteSaver` from the `langgraph-checkpoint-sqlite` dependency. Each instance has one database at `<state_dir>/checkpoints.sqlite`, which defaults to `.state/checkpoints.sqlite`. The runner opens and closes an async SQLite connection for each operation.

The graph checkpoint stores the `TurnRequest` with the model state. `Turns.respond` asks the runner to restore that request instead of rebuilding it from `turn.started`. The event log still identifies the pending approval and provides the contract cursor.

When a new turn starts, the runner continues from the latest completed graph checkpoint. It ignores checkpoints for interrupted turns. This keeps completed thread history and prevents an abandoned tool call from entering the next turn.

This decision supersedes ADR 0009's event-log reconstruction and ADR 0007's in-memory checkpointer.
