# One process owns event log delivery

One runtime process coordinates event appends and live subscriptions for an instance. Its lock makes the replay-to-live handoff atomic, while the JSONL record preserves sequences across restarts. Two processes must not append to the same instance at the same time; supporting that would require cross-process coordination.
