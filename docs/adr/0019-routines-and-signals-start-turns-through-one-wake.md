# Routines and signals start turns through one wake

Ticket #11 chose to make every non-user start of a turn a wake: one runtime entry point that takes an origin (user, routine, signal) and a payload, records the origin on `turn.started`, and renders the payload into the prompt as data rather than as a user message. A scheduled routine, a routine whose own code ran first, and a future inbound signal all use this path, so gate, recap, statistics and rating apply to them unchanged and the signal receiver only decides listening and auth. A routine run is an ordinary turn on a fresh thread; continuity between runs is the knowledge graph's job. A separate routine runner, or a cue placed in a user-role message that later code sniffs, was rejected because it would give proactive turns a second code path that every later feature (peer messages, subagent completions) would have to join.

## Consequences

- A routine is a directory, `routines/<name>/ROUTINE.md` plus an optional `run.py`, and the file is the source of truth: enable, disable, schedule, mode and budgets are frontmatter.
- The routine's own code is a tool: same decorator, loader and write flag, loaded from the routine's directory, never offered to the model, called only by the scheduler. Returning none means no wake and no model tokens.
- The scheduler is a reactor in the process that owns the event log (ADR 0006), started with the recap catch-up. `kinby serve` runs that boot without the REPL. One turn runs at a time per instance; no preemption in v1.
- Last run, next run and missed fires derive from the event log through the origin on `turn.started`; a missed schedule fires at most once at startup.
- Polling a source on a schedule is a code-step routine and is a fallback; a push is a signal and preferred when the provider offers one.
- "Event" keeps its meaning as a thread history record. Inbound stimuli are signals.
