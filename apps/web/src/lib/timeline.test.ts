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

  describe("a turn's recap", () => {
    const completed: Payload = { type: "turn.completed", input_tokens: 90, output_tokens: 10 }
    const recapped = (node: string | null): Payload => ({
      type: "memory.recapped",
      node,
      input_tokens: 40,
      output_tokens: 20,
    })

    it("is the episode it wrote", () => {
      const [turn] = replay([
        "turn-1",
        started("Fix the runtime"),
        completed,
        recapped("2026-09-28-0192-fix-the-runtime"),
      ]).turns

      expect(turn?.recap).toEqual({ kind: "episode", node: "2026-09-28-0192-fix-the-runtime" })
    })

    it("is no episode when it wrote none", () => {
      const [turn] = replay(["turn-1", started("Fix the runtime"), completed, recapped(null)]).turns

      expect(turn?.recap).toEqual({ kind: "none" })
    })

    it("failed with the message of a warning from the recap", () => {
      const [turn] = replay([
        "turn-1",
        started("Fix the runtime"),
        completed,
        {
          type: "warning",
          sources: ["recap"],
          message: "The turn recap failed: TimeoutError: the model did not answer",
        },
      ]).turns

      expect(turn?.recap).toEqual({
        kind: "failed",
        message: "The turn recap failed: TimeoutError: the model did not answer",
      })
    })

    it("is not a warning from anything else", () => {
      const [turn] = replay([
        "turn-1",
        started("Fix the runtime"),
        { type: "warning", sources: ["routines/nightly.md"], message: "Budget exceeds the cap." },
        completed,
      ]).turns

      expect(turn?.recap).toBeUndefined()
    })

    it("is none on a completed turn until it reports", () => {
      const [turn] = replay(["turn-1", started("Fix the runtime"), completed]).turns

      expect(turn?.end).toEqual({ kind: "done", tokens: 100 })
      expect(turn?.recap).toBeUndefined()
    })

    it("lands on its own turn when it reports after the next turn has started", () => {
      const [first, second] = replay(
        ["turn-1", started("Fix the runtime"), completed],
        ["turn-2", started("Now deploy it")],
        ["turn-1", recapped(null)],
      ).turns

      expect(first?.recap).toEqual({ kind: "none" })
      expect(second?.recap).toBeUndefined()
    })
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
        name: "write",
        arguments: write,
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
