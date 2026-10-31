import type { AcceptedResult, Event } from "@kinby/contract"
import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { openThread, type Payload, started, thread } from "@/components/thread-testing"

const accepted = (): AcceptedResult => ({ thread_id: "t1", turn_id: "turn-2", sequence: 9 })
const completed: Payload = { type: "turn.completed", input_tokens: 1_200, output_tokens: 300 }

/** Open thread t1, answering from `answers`, on `events` replayed. */
async function reopen(events: Event[], answers: Parameters<typeof openThread>[0] = {}) {
  const opened = openThread(answers)
  await act(async () => {
    opened.subscription().subscribed(events.length)
    for (const event of events) opened.subscription().deliver(event)
  })
  return opened
}

/** The turn that asked `request`. */
function turnOf(request: string) {
  const turn = screen.getByText(request).closest("[data-slot=message-scroller-item]")
  if (!(turn instanceof HTMLElement)) throw new Error(`No turn asked ${request}.`)
  return within(turn)
}

describe("how a turn ends", () => {
  describe("a finished turn", () => {
    it("ends in Copy, Good turn and Bad turn, then its steps, tokens and time", async () => {
      const events = thread(
        ["turn-1", started("Fix the runtime")],
        ["turn-1", { type: "tool.call", call_id: "c1", name: "read", arguments: { path: "a" } }],
        ["turn-1", completed],
      )
      events[2] = { ...(events[2] as Event), timestamp: "2026-09-28T10:01:12Z" }
      await reopen(events)

      for (const name of ["Copy", "Good turn", "Bad turn"]) {
        expect(screen.getByRole("button", { name })).toBeDefined()
      }
      expect(screen.getByText("1 step · 1.5k tokens · 1m 12s")).toBeDefined()
    })

    it("has no row while it runs", async () => {
      await reopen(
        thread(
          ["turn-1", started("Fix the runtime")],
          ["turn-1", { type: "message.delta", text: "Looking." }],
        ),
      )

      expect(screen.queryByRole("button", { name: "Copy" })).toBeNull()
    })

    it("copies its reply as Markdown", async () => {
      const user = userEvent.setup()
      await reopen(
        thread(
          ["turn-1", started("Fix the runtime")],
          ["turn-1", { type: "message.delta", text: "## The plan" }],
          ["turn-1", { type: "tool.call", call_id: "c1", name: "read", arguments: { path: "a" } }],
          ["turn-1", { type: "message.delta", text: "Fixed it **for good**." }],
          ["turn-1", completed],
        ),
      )

      await user.click(screen.getByRole("button", { name: "Copy" }))

      expect(await navigator.clipboard.readText()).toBe("## The plan\n\nFixed it **for good**.")
    })

    it("rates the turn good, from the keyboard too", async () => {
      const user = userEvent.setup()
      const { sent } = await reopen(
        thread(["turn-1", started("Fix the runtime")], ["turn-1", completed]),
        { "thread.turn.rate": accepted },
      )

      screen.getByRole("button", { name: "Copy" }).focus()
      await user.tab()
      await user.keyboard("{Enter}")

      expect(sent()).toEqual([
        {
          method: "thread.turn.rate",
          params: { thread_id: "t1", turn_id: "turn-1", verdict: "good" },
        },
      ])
    })

    it("rates the turn bad with the reason the user gives", async () => {
      const user = userEvent.setup()
      const { sent } = await reopen(
        thread(["turn-1", started("Fix the runtime")], ["turn-1", completed]),
        { "thread.turn.rate": accepted },
      )

      await user.click(screen.getByRole("button", { name: "Bad turn" }))
      await user.type(
        screen.getByRole("textbox", { name: "What went wrong?" }),
        "It broke the build",
      )
      await user.click(screen.getByRole("button", { name: "Rate" }))

      expect(sent()).toEqual([
        {
          method: "thread.turn.rate",
          params: {
            thread_id: "t1",
            turn_id: "turn-1",
            verdict: "bad",
            reason: "It broke the build",
          },
        },
      ])
      expect(screen.queryByRole("textbox", { name: "What went wrong?" })).toBeNull()
    })

    it("rates the turn bad without a reason", async () => {
      const user = userEvent.setup()
      const { sent } = await reopen(
        thread(["turn-1", started("Fix the runtime")], ["turn-1", completed]),
        { "thread.turn.rate": accepted },
      )

      await user.click(screen.getByRole("button", { name: "Bad turn" }))
      await user.keyboard("{Enter}")

      expect(sent()).toEqual([
        {
          method: "thread.turn.rate",
          params: { thread_id: "t1", turn_id: "turn-1", verdict: "bad" },
        },
      ])
    })

    it("says why a rating did not go through", async () => {
      const user = userEvent.setup()
      await reopen(thread(["turn-1", started("Fix the runtime")], ["turn-1", completed]))

      await user.click(screen.getByRole("button", { name: "Good turn" }))

      expect(await screen.findByText("The stub has no answer for thread.turn.rate.")).toBeDefined()
    })

    it("shows the current rating pressed", async () => {
      await reopen(
        thread(
          ["turn-1", started("Fix the runtime")],
          ["turn-1", completed],
          ["turn-1", { type: "turn.rated", verdict: "bad" }],
          ["turn-1", { type: "turn.rated", verdict: "good" }],
          ["turn-2", started("Deploy it")],
          ["turn-2", completed],
        ),
      )

      const pressed = (request: string, name: string) =>
        turnOf(request).getByRole("button", { name }).getAttribute("aria-pressed")
      expect(pressed("Fix the runtime", "Good turn")).toBe("true")
      expect(pressed("Fix the runtime", "Bad turn")).toBe("false")
      expect(pressed("Deploy it", "Good turn")).toBe("false")
      expect(pressed("Deploy it", "Bad turn")).toBe("false")
    })
  })

  describe("a turn's recap", () => {
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

    it("ends each turn with what the recap left, once it reports", async () => {
      await reopen(
        thread(
          ["turn-1", started("Fix the runtime")],
          ["turn-1", completed],
          ["turn-2", started("Deploy it")],
          ["turn-2", { type: "turn.interrupted" }],
          ["turn-3", started("Tidy the notes")],
          ["turn-3", { type: "turn.failed", code: "BUDGET_EXCEEDED", message: "No steps left." }],
          ["turn-4", started("Say hi")],
          ["turn-4", completed],
          // A recap runs after its turn, so it can report after the next turn has started.
          ["turn-1", recapped("2026-09-28-0192-fix-the-runtime")],
          ["turn-2", recapped(null)],
          ["turn-3", failed],
        ),
      )

      expect(turnOf("Fix the runtime").queryByRole("link", { name: "Recapped" })).not.toBeNull()
      expect(turnOf("Deploy it").queryByText("Nothing to recap")).not.toBeNull()
      expect(turnOf("Tidy the notes").queryByText("Recap failed")).not.toBeNull()
      for (const outcome of ["Recapped", "Nothing to recap", "Recap failed"]) {
        expect(turnOf("Say hi").queryByText(outcome)).toBeNull()
      }
    })

    it("links Recapped to its episode on the Memory page, as a new history entry", async () => {
      window.history.replaceState(null, "", "/instances/hub-ada/threads/t1")
      await reopen(
        thread(
          ["turn-1", started("Fix the runtime")],
          ["turn-1", completed],
          ["turn-1", recapped("2026-09-28-0192-fix-the-runtime")],
        ),
      )
      const link = screen.getByRole("link", { name: "Recapped" })
      const entries = window.history.length

      await userEvent.click(link)

      const episode = "/instances/hub-ada/memory/2026-09-28-0192-fix-the-runtime"
      expect(link.getAttribute("href")).toBe(episode)
      expect(window.location.pathname).toBe(episode)
      expect(window.history.length).toBe(entries + 1)
    })

    it("shows a recap that reports live, after the turn has completed", async () => {
      const { subscription } = await reopen(
        thread(["turn-1", started("Fix the runtime")], ["turn-1", completed]),
      )
      expect(screen.queryByText("Nothing to recap")).toBeNull()

      await act(async () =>
        subscription().deliver({
          sequence: 3,
          thread_id: "t1",
          turn_id: "turn-1",
          timestamp: "2026-09-28T10:00:30Z",
          payload: recapped(null),
        }),
      )

      expect(screen.getByText("Nothing to recap")).toBeDefined()
    })

    it("shows why the recap failed on hover", async () => {
      await reopen(
        thread(["turn-1", started("Fix the runtime")], ["turn-1", completed], ["turn-1", failed]),
      )

      await userEvent.hover(screen.getByText("Recap failed"))

      expect(
        await screen.findByText("The turn recap failed: TimeoutError: the model did not answer"),
      ).toBeDefined()
    })
  })

  describe("a failed turn", () => {
    const failed: Payload = {
      type: "turn.failed",
      code: "BUDGET_EXCEEDED",
      message: "The turn exceeded the steps budget of 1.",
    }

    it("ends in an alert with its message and code, and no row", async () => {
      await reopen(thread(["turn-1", started("Fix the runtime")], ["turn-1", failed]))

      const alert = within(screen.getByRole("alert"))
      expect(alert.getByText("The turn exceeded the steps budget of 1.")).toBeDefined()
      expect(alert.getByText("BUDGET_EXCEEDED")).toBeDefined()
      expect(screen.queryByRole("button", { name: "Copy" })).toBeNull()
    })

    it("retries a turn the user started with its request", async () => {
      const user = userEvent.setup()
      const { sent } = await reopen(
        thread(["turn-1", started("Fix the runtime")], ["turn-1", failed]),
        { "thread.turn.start": accepted },
      )

      await user.click(screen.getByRole("button", { name: "Retry" }))

      expect(sent()).toEqual([
        { method: "thread.turn.start", params: { thread_id: "t1", message: "Fix the runtime" } },
      ])
    })

    it("says why a retry did not go through", async () => {
      const user = userEvent.setup()
      await reopen(thread(["turn-1", started("Fix the runtime")], ["turn-1", failed]))

      await user.click(screen.getByRole("button", { name: "Retry" }))

      expect(await screen.findByText("The stub has no answer for thread.turn.start.")).toBeDefined()
    })

    it("offers no retry on a turn a routine started", async () => {
      await reopen(
        thread(
          [
            "turn-1",
            {
              type: "turn.started",
              message: "Summarize the night's email",
              model: "claude",
              origin: { kind: "routine", name: "nightly-digest", trigger: "scheduled" },
            },
          ],
          ["turn-1", failed],
        ),
      )

      expect(screen.getByRole("alert")).toBeDefined()
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull()
    })
  })

  it("ends a stopped turn in a Stopped line, and no row", async () => {
    await reopen(
      thread(["turn-1", started("Fix the runtime")], ["turn-1", { type: "turn.interrupted" }]),
    )

    expect(screen.getByText("Stopped")).toBeDefined()
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull()
  })
})
