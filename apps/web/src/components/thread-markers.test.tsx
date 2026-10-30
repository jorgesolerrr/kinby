import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { openThread, type Payload, started, thread } from "@/components/thread-testing"

describe("a thread's panel", () => {
  it("marks a failed turn's end as destructive, and a done turn's as not", async () => {
    const events = thread(
      ["turn-1", started("Fix the runtime")],
      ["turn-1", { type: "turn.completed", input_tokens: 1_200, output_tokens: 300 }],
      ["turn-2", started("Fix it again")],
      [
        "turn-2",
        {
          type: "turn.failed",
          code: "BUDGET_EXCEEDED",
          message: "The turn exceeded the steps budget of 1.",
        },
      ],
    )
    const { subscription } = openThread()

    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    const variant = (text: string) =>
      screen.getByText(text).closest("[data-slot=marker]")?.getAttribute("data-variant")
    expect(
      variant(
        "Failed: The turn exceeded the steps budget of 1. (BUDGET_EXCEEDED). Send a message to try again.",
      ),
    ).toBe("destructive")
    expect(variant("Done · 0 steps · 1,500 tokens")).toBe("default")
  })

  describe("a turn's recap", () => {
    const completed: Payload = { type: "turn.completed", input_tokens: 90, output_tokens: 10 }
    const recapped = (node: string | null): Payload => ({
      type: "memory.recapped",
      node,
      input_tokens: 40,
      output_tokens: 20,
    })
    const failed: Payload = {
      type: "warning",
      sources: ["recap"],
      message: "The turn recap failed: TimeoutError: the model did not answer",
    }
    /** The marker on the turn that asked `request`. */
    const recapOf = (request: string, marker: string) => {
      const turn = screen.getByText(request).closest("[data-slot=message-scroller-item]")
      if (!(turn instanceof HTMLElement)) throw new Error(`No turn asked ${request}.`)
      return within(turn).queryByText(marker)
    }

    it("ends each turn with what the recap left, once it reports", async () => {
      const events = thread(
        ["turn-1", started("Fix the runtime")],
        ["turn-1", completed],
        ["turn-2", started("Deploy it")],
        ["turn-2", completed],
        ["turn-3", started("Tidy the notes")],
        ["turn-3", completed],
        ["turn-4", started("Say hi")],
        ["turn-4", completed],
        // A recap runs after its turn, so it can report after the next turn has started.
        ["turn-1", recapped("2026-09-28-0192-fix-the-runtime")],
        ["turn-2", recapped(null)],
        ["turn-3", failed],
      )
      const { subscription } = openThread()

      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })

      expect(recapOf("Fix the runtime", "Recapped")).not.toBeNull()
      expect(recapOf("Deploy it", "No episode")).not.toBeNull()
      expect(recapOf("Tidy the notes", "Recap failed")).not.toBeNull()
      for (const marker of ["Recapped", "No episode", "Recap failed"]) {
        expect(recapOf("Say hi", marker)).toBeNull()
      }
      // Only the episode has somewhere to open.
      expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual(["Recapped"])
    })

    it("links Recapped to its episode on the Memory page, as a new history entry", async () => {
      window.history.replaceState(null, "", "/instances/hub-ada/threads/t1")
      const events = thread(
        ["turn-1", started("Fix the runtime")],
        ["turn-1", completed],
        ["turn-1", recapped("2026-09-28-0192-fix-the-runtime")],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      const link = screen.getByRole("link", { name: "Recapped" })
      const entries = window.history.length

      await userEvent.click(link)

      const episode = "/instances/hub-ada/memory/2026-09-28-0192-fix-the-runtime"
      expect(link.getAttribute("href")).toBe(episode)
      expect(window.location.pathname).toBe(episode)
      expect(window.history.length).toBe(entries + 1)
    })

    it("shows a recap that reports live, after the turn has completed", async () => {
      const events = thread(["turn-1", started("Fix the runtime")], ["turn-1", completed])
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      expect(screen.getByText("Done · 0 steps · 100 tokens")).toBeDefined()
      expect(screen.queryByText("No episode")).toBeNull()

      await act(async () =>
        subscription().deliver({
          sequence: 3,
          thread_id: "t1",
          turn_id: "turn-1",
          timestamp: "2026-09-28T10:00:30Z",
          payload: recapped(null),
        }),
      )

      expect(screen.getByText("No episode")).toBeDefined()
    })

    it("shows why the recap failed on hover of its marker, a button", async () => {
      const events = thread(
        ["turn-1", started("Fix the runtime")],
        ["turn-1", completed],
        ["turn-1", failed],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      expect(
        screen.getByText("The turn recap failed: TimeoutError: the model did not answer"),
      ).toBeDefined()
    })
  })
})
