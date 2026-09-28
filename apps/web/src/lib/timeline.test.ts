import type { Event } from "@kinby/contract"
import { describe, expect, it } from "vitest"

import { EMPTY_TIMELINE, project, type Timeline } from "@/lib/timeline"

type Payload = Event["payload"]

/** Project a thread's events, numbered from 1 and a second apart, onto an empty timeline. */
function replay(...turns: [turnId: string, ...payloads: Payload[]][]): Timeline {
  const events = turns.flatMap(([turnId, ...payloads]) =>
    payloads.map((payload) => ({ turn_id: turnId, payload })),
  )
  return events
    .map(({ turn_id, payload }, index): Event => ({
      sequence: index + 1,
      thread_id: "t1",
      turn_id,
      timestamp: `2026-09-28T10:00:${String(index).padStart(2, "0")}Z`,
      payload,
    }))
    .reduce(project, EMPTY_TIMELINE)
}

const started = (message: string): Payload => ({
  type: "turn.started",
  message,
  model: "claude",
  origin: { kind: "user" },
})

describe("a thread's timeline", () => {
  it("shows a completed turn as its request, its text and tool steps in order, and done with its tokens", () => {
    const timeline = replay([
      "turn-1",
      started("Fix the runtime"),
      { type: "message.delta", text: "Let me look " },
      { type: "message.delta", text: "first." },
      { type: "tool.call", call_id: "c1", name: "read", arguments: { path: "runtime.py" } },
      {
        type: "tool.gated",
        call_id: "c1",
        name: "read",
        action: "allow",
        decided_by: "policy",
        rule: "reads are allowed",
      },
      {
        type: "tool.result",
        call_id: "c1",
        name: "read",
        output: "…",
        error: false,
        duration_ms: 38,
      },
      { type: "message.delta", text: "Done." },
      {
        type: "model.completed",
        model: "claude",
        input_tokens: 90,
        output_tokens: 10,
        duration_ms: 900,
      },
      { type: "turn.completed", input_tokens: 1_200, output_tokens: 300 },
    ])

    expect(timeline).toEqual({
      sequence: 9,
      turns: [
        {
          turnId: "turn-1",
          startedBy: { kind: "user" },
          startedAt: "2026-09-28T10:00:00Z",
          request: "Fix the runtime",
          steps: [
            { kind: "text", text: "Let me look first." },
            {
              kind: "tool",
              callId: "c1",
              name: "read",
              arguments: { path: "runtime.py" },
              gate: { action: "allow", decidedBy: "policy", rule: "reads are allowed" },
              durationMs: 38,
            },
            { kind: "text", text: "Done." },
          ],
          end: { kind: "done", tokens: 1_500 },
        },
      ],
    })
  })

  it("ends a failed turn with the code and message", () => {
    const [turn] = replay([
      "turn-1",
      started("Deploy it"),
      { type: "turn.failed", code: "BUDGET_EXCEEDED", message: "The steps budget ran out." },
    ]).turns

    expect(turn?.end).toEqual({
      kind: "failed",
      code: "BUDGET_EXCEEDED",
      message: "The steps budget ran out.",
    })
  })

  it("ends an interrupted turn as stopped", () => {
    const [turn] = replay([
      "turn-1",
      started("Deploy it"),
      { type: "message.delta", text: "Starting" },
      { type: "turn.interrupted" },
    ]).turns

    expect(turn?.end).toEqual({ kind: "stopped" })
  })

  it("names the routine that started a turn", () => {
    const [turn] = replay([
      "turn-1",
      {
        type: "turn.started",
        message: "Summarize the night's email",
        model: "claude",
        origin: { kind: "routine", name: "nightly-digest", trigger: "scheduled" },
      },
    ]).turns

    expect(turn?.startedBy).toEqual({ kind: "routine", name: "nightly-digest" })
    expect(turn?.request).toBe("Summarize the night's email")
  })

  describe("a running turn", () => {
    const write = { path: "runtime.py", content: "fixed" }
    const parked: Payload[] = [
      started("Fix the runtime"),
      { type: "tool.call", call_id: "c1", name: "read", arguments: { path: "runtime.py" } },
      { type: "tool.call", call_id: "c2", name: "write", arguments: write },
      {
        type: "approval.requested",
        approval_id: "a1",
        name: "write",
        arguments: write,
        rule: "ask mode: writes need approval",
      },
    ]

    it("has no end, and is parked on the call its approval asks about", () => {
      const [turn] = replay(["turn-1", ...parked]).turns

      expect(turn?.end).toBeUndefined()
      expect(turn?.approval).toEqual({
        approvalId: "a1",
        callId: "c2",
        rule: "ask mode: writes need approval",
      })
    })

    it("stops waiting once the turn moves on, the call approved by the user", () => {
      const [turn] = replay([
        "turn-1",
        ...parked,
        {
          type: "tool.gated",
          call_id: "c1",
          name: "read",
          action: "allow",
          decided_by: "policy",
          rule: "reads are allowed",
        },
        {
          type: "tool.gated",
          call_id: "c2",
          name: "write",
          action: "allow",
          decided_by: "user",
          rule: "ask mode: writes need approval",
        },
      ]).turns

      expect(turn?.end).toBeUndefined()
      expect(turn?.approval).toBeUndefined()
      expect(turn?.steps.at(-1)).toMatchObject({
        callId: "c2",
        gate: { action: "allow", decidedBy: "user" },
      })
    })
  })
})
