# A re-enable after an automatic disable resets the failure streak

The scheduler writes `routine.failures.reset` when a routine is enabled and its current failure streak ended in an automatic disable. The event goes on the thread and turn of the failed firing that caused the disable, the turn that already holds its `routine.failure.handled` ([ADR 0023](0023-routine-failure-policy-is-marked-in-the-event-log.md)). From that event on the fold counts 0 failures for the routine and no longer sees a disabled streak, so the scheduler writes it once. The disable notice stays in the routine's notices.

The scheduler runs the check on every pass, before the failure policy. `routine.list` runs it before it folds history, and `routine.run` before it accepts the firing, so a manual run's failure lands after the reset and counts as the first. It reads only the event log and the routine's file, so it covers the app, the agent's tool, `routine.write` and a hand edit of `ROUTINE.md` alike, and it still holds after a restart.

Having each enable path write the event was rejected: only the agent's tool leaves an event, and a hand edit has no turn to put one on. Resetting on any enable was rejected too. Enabling a routine below the threshold is not a recovery, and resetting then would hide a streak that is still failing.
