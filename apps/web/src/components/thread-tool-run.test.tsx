import { act, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { openThread, started, thread } from "@/components/thread-testing"

describe("a thread's panel", () => {
  it("marks how long a call you approved took once it ran", async () => {
    const events = thread(
      ["turn-1", started("Deploy it")],
      [
        "turn-1",
        { type: "tool.call", call_id: "c1", name: "bash", arguments: { command: "make deploy" } },
      ],
      [
        "turn-1",
        {
          type: "tool.gated",
          call_id: "c1",
          name: "bash",
          action: "allow",
          decided_by: "user",
          rule: "mode.ask.write",
        },
      ],
      [
        "turn-1",
        {
          type: "tool.result",
          call_id: "c1",
          name: "bash",
          output: "",
          error: false,
          duration_ms: 12,
        },
      ],
    )
    const { subscription } = openThread()
    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    expect(screen.getByText("bash make deploy · approved by you · 12 ms")).toBeDefined()
  })

  describe("a call whose result is an error", () => {
    /** Open a thread whose one call, gated by `decidedBy`, returns an error that took `durationMs`. */
    async function openFailedCall(decidedBy: "policy" | "user", durationMs: number | null) {
      const events = thread(
        ["turn-1", started("List the routines")],
        [
          "turn-1",
          {
            type: "tool.call",
            call_id: "c1",
            name: "hub_read",
            arguments: { path: "routine.list" },
          },
        ],
        [
          "turn-1",
          {
            type: "tool.gated",
            call_id: "c1",
            name: "hub_read",
            action: "allow",
            decided_by: decidedBy,
            rule: "reads are allowed",
          },
        ],
        [
          "turn-1",
          {
            type: "tool.result",
            call_id: "c1",
            name: "hub_read",
            output: 'Tool "hub_read" is not available in this turn.',
            error: true,
            duration_ms: durationMs,
          },
        ],
        ["turn-1", { type: "turn.completed", input_tokens: 90, output_tokens: 10 }],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
    }

    it("is failed with its error, not running, when the result has no duration", async () => {
      await openFailedCall("policy", null)

      expect(
        screen.getByText(
          'hub_read routine.list · failed: Tool "hub_read" is not available in this turn.',
        ),
      ).toBeDefined()
      expect(screen.queryByText(/running/)).toBeNull()
    })

    it("is failed with its error and how long it took, when the result has a duration", async () => {
      await openFailedCall("policy", 12)

      expect(
        screen.getByText(
          'hub_read routine.list · failed after 12 ms: Tool "hub_read" is not available in this turn.',
        ),
      ).toBeDefined()
    })

    it("is approved by you, then failed, when you approved it", async () => {
      await openFailedCall("user", 12)

      expect(
        screen.getByText(
          'hub_read routine.list · approved by you · failed after 12 ms: Tool "hub_read" is not available in this turn.',
        ),
      ).toBeDefined()
    })
  })

  it("marks a call that returned without a duration as done, not running", async () => {
    const events = thread(
      ["turn-1", started("Read it")],
      [
        "turn-1",
        { type: "tool.call", call_id: "c1", name: "read", arguments: { path: "runtime.py" } },
      ],
      [
        "turn-1",
        {
          type: "tool.gated",
          call_id: "c1",
          name: "read",
          action: "allow",
          decided_by: "policy",
          rule: "reads are allowed",
        },
      ],
      [
        "turn-1",
        {
          type: "tool.result",
          call_id: "c1",
          name: "read",
          output: "…",
          error: false,
          duration_ms: null,
        },
      ],
    )
    const { subscription } = openThread()
    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    expect(screen.getByText("read runtime.py · done")).toBeDefined()
  })
})
