import type { Event, NotificationRaised } from "@kinby/contract"
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest"

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
  // The browser's time zone, four hours behind UTC in the autumn.
  beforeAll(() => vi.stubEnv("TZ", "America/New_York"))
  afterAll(() => vi.unstubAllEnvs())

  it("shows a completed turn as its request, its text and tool steps in order, and done with its tokens and how long it took", () => {
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
          startedOn: "2026-09-28",
          request: "Fix the runtime",
          steps: [
            { kind: "text", text: "Let me look first." },
            {
              kind: "tool",
              callId: "c1",
              name: "read",
              arguments: { path: "runtime.py" },
              gate: { action: "allow", decidedBy: "policy", rule: "reads are allowed" },
              result: { output: "…", error: false, durationMs: 38 },
            },
            { kind: "text", text: "Done." },
          ],
          end: { kind: "done", tokens: 1_500, durationMs: 8_000 },
        },
      ],
    })
  })

  it("keeps a call's output and marks it failed when its result is an error, though the result has no duration", () => {
    const [turn] = replay([
      "turn-1",
      started("List the routines"),
      { type: "tool.call", call_id: "c1", name: "hub_read", arguments: { path: "routine.list" } },
      {
        type: "tool.result",
        call_id: "c1",
        name: "hub_read",
        output: 'Tool "hub_read" is not available in this turn.',
        error: true,
        duration_ms: null,
      },
    ]).turns

    expect(turn?.steps).toEqual([
      {
        kind: "tool",
        callId: "c1",
        name: "hub_read",
        arguments: { path: "routine.list" },
        result: { output: 'Tool "hub_read" is not available in this turn.', error: true },
      },
    ])
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

  it("shows each notification the turn raised, with the hub's answer to it", () => {
    const raised = (title: string, options: NotificationRaised["options"]): Payload => ({
      type: "notification.raised",
      source: "nightly-digest",
      title,
      text: "Three need an answer.",
      options,
    })
    const [turn] = replay([
      "turn-1",
      started("Sort the inbox"),
      raised("Inbox sorted", ["Archive them", "Leave them"]),
      raised("Second one", []),
      raised("Third one", []),
      { type: "notification.accepted", raised: 2, notification_id: "n1" },
      { type: "notification.refused", raised: 3, reason: "The daily cap is reached." },
    ]).turns

    expect(turn?.steps).toEqual([
      {
        kind: "notification",
        raised: 2,
        title: "Inbox sorted",
        text: "Three need an answer.",
        options: ["Archive them", "Leave them"],
        hub: { kind: "accepted", notificationId: "n1" },
      },
      {
        kind: "notification",
        raised: 3,
        title: "Second one",
        text: "Three need an answer.",
        options: [],
        hub: { kind: "refused", reason: "The daily cap is reached." },
      },
      {
        kind: "notification",
        raised: 4,
        title: "Third one",
        text: "Three need an answer.",
        options: [],
      },
    ])
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

  it("names the factory and step that started a turn", () => {
    const [turn] = replay([
      "turn-1",
      {
        type: "turn.started",
        message: "Review the branch.",
        model: "claude",
        origin: { kind: "factory-run", factory: "software", run_id: "run-1", step: "review" },
      },
    ]).turns

    expect(turn?.startedBy).toEqual({ kind: "factory-run", factory: "software", step: "review" })
  })

  it("starts each turn on its day in the browser's time zone, though both share a UTC date", () => {
    const [before, after] = [
      ["turn-1", "2026-09-29T03:59:00Z"],
      ["turn-2", "2026-09-29T04:01:00Z"],
    ]
      .map(([turnId, timestamp], index): Event => ({
        sequence: index + 1,
        thread_id: "t1",
        turn_id: turnId,
        timestamp,
        payload: started("Morning"),
      }))
      .reduce(project, EMPTY_TIMELINE).turns

    expect(before?.startedOn).toBe("2026-09-28")
    expect(after?.startedOn).toBe("2026-09-29")
  })

  describe("a turn's rating", () => {
    const completed: Payload = { type: "turn.completed", input_tokens: 90, output_tokens: 10 }

    it("is none while nobody rated the turn", () => {
      const [turn] = replay(["turn-1", started("Fix the runtime"), completed]).turns

      expect(turn?.rating).toBeUndefined()
    })

    it("is the latest verdict on the turn", () => {
      const [turn] = replay([
        "turn-1",
        started("Fix the runtime"),
        completed,
        { type: "turn.rated", verdict: "good" },
        { type: "turn.rated", verdict: "bad", reason: "It broke the build" },
      ]).turns

      expect(turn?.rating).toBe("bad")
    })

    it("lands on its own turn when it arrives after the next turn has started", () => {
      const live = project(
        replay(["turn-1", started("Fix the runtime"), completed], ["turn-2", started("Thanks")]),
        {
          sequence: 4,
          thread_id: "t1",
          turn_id: "turn-1",
          timestamp: "2026-09-28T10:05:00Z",
          payload: { type: "turn.rated", verdict: "good" },
        },
      )

      const [first, second] = live.turns
      expect(first?.rating).toBe("good")
      expect(second?.rating).toBeUndefined()
    })
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

      expect(turn?.end).toEqual({ kind: "done", tokens: 100, durationMs: 1_000 })
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
    it("is parked on a takeover, with its reason, until the turn moves on", () => {
      const reason = "Sign in to LinkedIn"
      const takeover: Payload[] = [
        started("Check LinkedIn"),
        { type: "tool.call", call_id: "c1", name: "browser_takeover", arguments: { reason } },
        { type: "takeover.requested", takeover_id: "k1", reason },
      ]
      const gated: Payload = {
        type: "tool.gated",
        call_id: "c1",
        name: "browser_takeover",
        action: "allow",
        decided_by: "policy",
        rule: "mode.full-access.write",
      }

      const [parked] = replay(["turn-1", ...takeover]).turns
      const [resumed] = replay(["turn-1", ...takeover, gated]).turns

      expect(parked?.takeover).toEqual({ takeoverId: "k1", reason })
      expect(resumed?.takeover).toBeUndefined()
    })

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

    it("still waits once the turn records the notification that tells the user", () => {
      const [turn] = replay([
        "turn-1",
        ...parked,
        {
          type: "notification.raised",
          source: "Fix the runtime",
          title: "Approval waiting",
          text: "write: path=runtime.py, content=fixed",
          options: ["Approve", "Deny"],
        },
        { type: "notification.accepted", raised: 5, notification_id: "n1" },
      ]).turns

      expect(turn?.approval?.approvalId).toBe("a1")
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

    describe("the notification that asks about its approval", () => {
      const notified: Payload = {
        type: "notification.raised",
        source: "Fix the runtime",
        title: "Approval waiting",
        text: "write: path=runtime.py, content=fixed",
        options: ["Approve", "Deny"],
        approval: {
          turn_id: "turn-1",
          request: {
            type: "approval.requested",
            approval_id: "a1",
            name: "write",
            arguments: write,
            rule: "ask mode: writes need approval",
          },
        },
      }
      const gated = (action: "allow" | "deny"): Payload => ({
        type: "tool.gated",
        call_id: "c2",
        name: "write",
        action,
        decided_by: "user",
        rule: "ask mode: writes need approval",
      })
      const notification = (...after: Payload[]) =>
        replay(["turn-1", ...parked, notified, ...after]).turns[0]?.steps.find(
          (step) => step.kind === "notification",
        )

      it("waits on the approval's call while the turn is parked", () => {
        expect(notification()).toMatchObject({ approval: { callId: "c2" } })
        expect(notification()).not.toHaveProperty("approval.settled")
      })

      it("settles as approved once the user allows the call", () => {
        expect(notification(gated("allow"))).toMatchObject({
          approval: { callId: "c2", settled: "allow" },
        })
      })

      it("settles as denied once the user denies the call", () => {
        expect(notification(gated("deny"))).toMatchObject({
          approval: { callId: "c2", settled: "deny" },
        })
      })

      it("settles as no longer waiting when the turn ends before a decision", () => {
        expect(notification({ type: "turn.interrupted" })).toMatchObject({
          approval: { callId: "c2", settled: "ended" },
        })
      })

      it("keeps its decision once the turn ends", () => {
        expect(
          notification(gated("deny"), {
            type: "turn.completed",
            input_tokens: 9,
            output_tokens: 1,
          }),
        ).toMatchObject({ approval: { settled: "deny" } })
      })
    })
  })
})
