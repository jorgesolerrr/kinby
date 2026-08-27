# The event log persists as JSONL

The transcript store writes one validated event envelope per line to `.state/events.jsonl`. JSONL keeps appends and recovery inspectable without adding a database dependency. A future storage change must migrate durable thread history.
