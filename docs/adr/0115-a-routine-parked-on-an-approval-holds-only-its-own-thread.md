# A routine parked on an approval holds only its own thread

[ADR 0099](0099-dreaming-runs-in-stages-as-routines-whose-body-is-the-lens.md) let a **dreaming stage** parked on an **approval** hold only its own thread. Every other parked turn held the whole **instance**. On 2026-11-01 the `cec-backend` coder's weekly `reflection` routine parked on a `bash` call at 04:00 UTC. Its routine file had no `run: reflect`, so it was not a dreaming stage, and nobody answered. From then on every factory step waited on it, the hub's queue for that instance stalled behind the step, and the hourly `scan` piled up 79 **deliveries**. The only trace was "reflection: parked" in the routine list.

## Decision

- **A routine parked on an approval holds only its own thread**, whatever its code step. Other turns, routines and factory steps run while it waits.
- **Answering it takes the instance back like a new turn.** The answer is refused as busy while something else holds the instance, and the user answers again once it is free. This is the path ADR 0099 already gave dreaming stages.
- **A turn the user started, a home instance's ask and a factory step's own turn still hold the instance while parked.** Someone is waiting on each of them.

## Considered options

- **An approval deadline that denies the call.** Rejected. The instance stays jammed until the deadline, and a denied routine at `ask` asks again on its next call and parks once more.
- **A deadline that ends the turn**, the way an abandoned takeover does. Rejected for the same wait, and because a weekly routine's approvals are meant to be answered days later.
- **Letting factory steps go ahead of a parked routine.** Rejected. It frees steps only, and leaves the routines and user turns queued behind it.
- **Showing "held by a parked approval" on queued runs in the hub and the web app.** Not needed now. With this decision, what still holds an instance is a turn someone is watching.

## Consequences

- A routine can have more than one run parked, one per thread, when it fires again before the user answers.
- A parked routine's workspace changes stay in place while other turns run. Reverting its turn later restores its starting tree and undoes what ran in between, as reverting any earlier turn already does.
- `RoutineOrigin.dreaming_stage` no longer decides holding. The recap and the learning review still read it.
- `bash` stays a write: the gate never parses commands, so `mode: ask` asks before `gh issue view` as before any other command. A routine that should run read-only commands unasked uses a routine grant ([ADR 0112](0112-any-edit-to-a-routine-revokes-its-grants.md)) or a `[bash]` rule.
