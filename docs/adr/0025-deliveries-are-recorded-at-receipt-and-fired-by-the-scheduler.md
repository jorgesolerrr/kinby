# Deliveries are recorded at receipt and fired by the scheduler

Ticket #107 chose to make the receiver a recorder, not a caller of the wake path. When an authenticated signal arrives for a routine, the receiver creates a thread, appends a `signal.received` event holding the whole delivery, and answers 202. The scheduler, on its next pass, starts a routine turn on that thread with the signal trigger, reusing the turn id fixed at receipt, ordered by sequence against due schedules. The receiver never waits for the instance to be free and never starts a turn itself.

The ticket's own framing, and ADR 0019, had the receiver call the wake path directly. That fails the first real source: GitHub does not retry a failed webhook delivery, and a development-loop firing can hold the instance for an hour, so holding the request and answering 503 loses the issues the workflow exists to pick up. An in-memory queue answers fast but loses queued deliveries on stop. A separate durable inbox file would be a second run history next to the event log, which ADR 0019 rules out.

## Consequences

- The event log is the delivery queue. Pending deliveries are threads with a `signal.received` event and no `turn.started` for that turn id, derived by the same fold that derives routine history. Catch-up after a restart is the ordinary scheduler pass, and every pending delivery fires, one turn each, with no at-most-once rule.
- Every accepted delivery is a recorded turn, including one the code step filters out as no work. Failure policy, statistics and rating cover signal firings unchanged.
- Duplicate deliveries are dropped by provider delivery id when the routine names the header, because the id already sits in the log the scheduler reads.
- A delivery fires even if its routine was disabled after receipt, like a manual run. A disabled routine answers 410 to new deliveries and records nothing.
- `kinby run` boots the same scheduler, so pending deliveries also fire during a REPL session, waiting behind the user's turn as scheduled routines do.
