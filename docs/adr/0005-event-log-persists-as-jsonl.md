# The event log persists as JSONL

Superseded by [ADR 0116](0116-an-instance-keeps-its-transcript-store-in-sqlite.md).

The transcript store writes one validated event envelope per line to `.state/events.jsonl`. JSONL keeps appends and recovery inspectable without adding a database dependency. A future storage change must migrate durable thread history.
