// PROTOTYPE, throwaway. One instance, four threads, stored as the events `thread.subscribe` replays.
import type { Event, PermissionMode } from "@kinby/contract"

export const INSTANCE = {
  name: "Coder",
  model: "claude-sonnet-5",
  // Neither is readable over the contract today: the panel would have to guess both.
  defaultMode: "ask" as PermissionMode,
  ceiling: "auto" as PermissionMode,
  allowedModes: ["read-only", "ask", "auto"] as PermissionMode[],
}

export const MODES: { mode: PermissionMode; label: string; hint: string }[] = [
  { mode: "read-only", label: "Read-only", hint: "Writes are denied" },
  { mode: "ask", label: "Ask", hint: "Writes wait for you" },
  { mode: "auto", label: "Auto", hint: "Writes run, denylist still applies" },
  { mode: "full-access", label: "Full access", hint: "Everything runs" },
]

export type StubThread = { id: string; title: string | null; createdAt: string; events: Event[] }

type Payload = Event["payload"]

function build(threadId: string, turns: [string, Payload[]][], start: Date): Event[] {
  const events: Event[] = []
  let at = start.getTime()
  for (const [turnId, payloads] of turns)
    for (const payload of payloads) {
      at += 1500
      events.push({
        sequence: events.length + 1,
        thread_id: threadId,
        turn_id: turnId,
        timestamp: new Date(at).toISOString(),
        payload,
      })
    }
  return events
}

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000)

export function seedThreads(): StubThread[] {
  const flaky = "t-flaky"
  const brief = "t-brief"
  const lisbon = "t-lisbon"
  const docs = "t-docs"
  return [
    {
      id: flaky,
      title: "Fix the flaky hub drain test",
      createdAt: hoursAgo(3).toISOString(),
      events: build(
        flaky,
        [
          [
            "u1",
            [
              {
                type: "turn.started",
                message: "The drain test in tests/hub fails about one run in ten. Can you find out why?",
                model: INSTANCE.model,
                origin: { kind: "user" },
                permission_mode: "ask",
              },
              { type: "message.delta", text: "I'll run the test a few times and read the drain code. " },
              {
                type: "tool.call",
                call_id: "c1",
                name: "run_command",
                arguments: { command: "uv run pytest tests/hub/test_drain.py --count 20" },
                write: false,
              },
              { type: "tool.gated", call_id: "c1", name: "run_command", action: "allow", decided_by: "policy", rule: "allowlisted command" },
              { type: "tool.result", call_id: "c1", name: "run_command", error: false, output: "18 passed, 2 failed", duration_ms: 41200 },
              {
                type: "tool.call",
                call_id: "c2",
                name: "read_file",
                arguments: { path: "src/kinby/hub/drain.py" },
                write: false,
              },
              { type: "tool.gated", call_id: "c2", name: "read_file", action: "allow", decided_by: "policy", rule: "reads are allowed" },
              { type: "tool.result", call_id: "c2", name: "read_file", error: false, output: "async def drain(...)", duration_ms: 20 },
              {
                type: "message.delta",
                text: "Two of twenty runs fail. The test waits on a fixed 0.1 s sleep instead of the drained event, so a slow CI box loses the race.",
              },
              { type: "turn.completed", input_tokens: 12400, output_tokens: 610, outcome: "work" },
            ],
          ],
          [
            "u2",
            [
              {
                type: "turn.started",
                message: "Makes sense. Fix it and open a PR.",
                model: INSTANCE.model,
                origin: { kind: "user" },
                permission_mode: "ask",
              },
              { type: "message.delta", text: "I'll replace the sleep with an await on the drained event. " },
              {
                type: "tool.call",
                call_id: "c3",
                name: "write_file",
                arguments: { path: "tests/hub/test_drain.py", content: "…" },
                write: true,
              },
              {
                type: "approval.requested",
                approval_id: "a1",
                name: "write_file",
                arguments: { path: "tests/hub/test_drain.py", content: "…" },
                rule: "ask mode: writes need approval",
              },
            ],
          ],
        ],
        hoursAgo(3),
      ),
    },
    {
      id: brief,
      title: null,
      createdAt: hoursAgo(9).toISOString(),
      events: build(
        brief,
        [
          [
            "r1",
            [
              {
                type: "turn.started",
                message: "Run the morning-brief routine.",
                model: INSTANCE.model,
                origin: { kind: "routine", name: "morning-brief", trigger: "scheduled" },
              },
              {
                type: "message.delta",
                text: "Good morning. Three PRs wait for review, CI is green on main, and the babysitter has one PR merge-ready.",
              },
              { type: "turn.completed", input_tokens: 5200, output_tokens: 140, outcome: "work" },
            ],
          ],
        ],
        hoursAgo(9),
      ),
    },
    {
      id: lisbon,
      title: "Trip to Lisbon",
      createdAt: hoursAgo(30).toISOString(),
      events: build(
        lisbon,
        [
          [
            "l1",
            [
              { type: "mode.pinned", mode: "read-only" },
            ],
          ],
          [
            "l2",
            [
              {
                type: "turn.started",
                message: "Find me three quiet hotels in Alfama under 150 a night.",
                model: INSTANCE.model,
                origin: { kind: "user" },
                permission_mode: "read-only",
              },
              { type: "message.delta", text: "Searching now. " },
              {
                type: "turn.failed",
                code: "RESOURCE_EXHAUSTED",
                message: "The API rate limit was hit. Try again in a minute.",
              },
            ],
          ],
        ],
        hoursAgo(30),
      ),
    },
    { id: docs, title: "Draft the README intro", createdAt: hoursAgo(50).toISOString(), events: [] },
  ]
}
