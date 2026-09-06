# The scheduler disables repeatedly failing routines

The September 5 research review for [#113](https://github.com/jorgesolerrr/kinby/issues/113) adopted automatic disabling after ten failed firings. The scheduler derives the count from durable run history and writes `enabled: false` into `ROUTINE.md`. This write is authorized runtime behavior under the failure policy. Keeping the flag in the file preserves the routine's source of truth and stops broken routines from running indefinitely.

Code-step errors, model errors, and per-turn budget exhaustion count as failed firings. Successful work resets the count, including a successful manual run. A code step returning `None`, a user interruption, and a refusal at the daily cost limit neither increment nor reset it. A parked approval is not a failure. Count firings, not retries of the same firing.

Report the first failure and the automatic disable, and keep every failure in the history. `routine.list` and the REPL expose the status until channels provide delivery. Re-enabling requires an explicit edit or a future gated routine operation. A manual run never re-enables the routine by itself.

No hard minimum interval or enabled-routine cap is added. Deterministic checks may run frequently without model spend. The writing skill recommends suitable schedules and push sources when available.
