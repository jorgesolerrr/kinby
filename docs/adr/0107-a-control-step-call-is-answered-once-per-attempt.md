# A control step call is answered once per attempt

Amends [ADR 0048](0048-the-contract-crosses-the-network-in-typed-frames-one-socket-per-instance.md) for the hub's control calls. Completes [ADR 0102](0102-the-hub-never-charges-an-attempt-for-its-own-restart-or-a-lost-connection.md).

ADR 0102 says a lost control connection decides nothing: the hub asks the instance for the call's outcome and offers the step again only when the instance does not hold it. It left how the instance keeps that outcome to the control transport. Today it doesn't. Each `step.run` holds one WebSocket for the whole step, every call uses frame id `1`, and when the socket drops the instance finishes the step and throws its result away. ADR 0048 says a client never retries a unary call that was in flight, and chose that over idempotency keys.

[#632](https://github.com/jorgesolerrr/kinby/issues/632) asked whether to move control calls to HTTP and server-sent events for this. The drops behind it, on 2026-10-10, came from the 4 MB frame limit (#633), not from the socket. kinby's coder has held 11-minute steps on one socket with no drop.

## Decision

**The instance answers `step.run` once per attempt, keyed by run, step and attempt.**

- **One answer per key.** An unknown key starts the step. A key still running waits for that run and returns its result. A finished key returns the result the instance holds. A second send never starts a second session on the same run worktree.
- **The hub sends again.** When the connection closes before the instance answers, or the hub restarts during the step, the hub sends the same `step.run` again with backoff. A dropped attempt reuses its number, so the restart case sends the same key.
- **The instance keeps the latest outcome per run, on disk.** The run's next `step.run` replaces it, and the run's ended call drops it. Steps run one at a time per run, so the next send is the acknowledgement.
- **An instance restart fails the attempt.** The instance marks a key started before it runs the step. A key with a start and no outcome answers failed, "the instance restarted during the step", and spends a retry. A step that keeps killing its instance stops for a human instead of looping.
- **An answer must fit a frame.** The instance trims a result's summary to fit. An answer still over the frame limit fails the attempt; the hub does not ask for it again.
- **Step attempts only.** The needs-human and ended reports, drain, stats and `run.stopped` keep their current handling. Reports are already sent until they land and are safe to run twice. Drain already calls again on a lost socket.
- **A capability gates it.** The instance reports it in its health probe. Against an instance without it, the hub keeps failing the attempt on a lost connection, because a re-send there would start a second session.

Control calls stay on the WebSocket.

## Considered options

- **Move control calls to HTTP and SSE: start a step, get an id, follow its events, read the result by id.** Rejected for now. It also solves the problem, but it replaces the transport for a failure the transport did not cause. Only a separate result store was needed.
- **A separate `step.outcome` call that answers held, running or unknown.** Rejected. The hub would need its own branch for each answer, and a running key would still need a wait. Making `step.run` answer once per key gives the same result with no new method.
- **Keep every outcome until the hub acknowledges it.** Rejected. An acknowledgement call can be lost too, and the next `step.run` for the run already says the hub has the previous one.
- **Treat an instance restart as unknown and run the step again for free.** Rejected. A step that runs its instance out of memory would loop forever.
