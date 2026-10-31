import { CallError, type Event, type PermissionMode } from "@kinby/contract"
import { act, fireEvent, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import {
  instanceClient,
  openThread,
  started,
  thread,
  threadSummary,
} from "@/components/thread-testing"

/** How the instance lists thread t1, as `listed()` returns it, under `ceiling`. */
function listing(listed = () => threadSummary(), ceiling: PermissionMode = "full-access") {
  return () => ({ threads: [listed()], ceiling, cursor: null })
}

const modePicker = () => screen.findByRole("combobox", { name: "Mode" })

describe("a thread's panel", () => {
  it("puts focus in the composer once the replay has loaded", async () => {
    const { subscription } = openThread()

    await act(async () => subscription().subscribed(0))

    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Message Ada" }))
  })

  it("starts a turn with the message when Enter is pressed", async () => {
    const { sent, subscription } = openThread({
      "thread.turn.start": () => ({ sequence: 1, thread_id: "t1", turn_id: "turn-1" }),
    })
    await act(async () => subscription().subscribed(0))
    const user = userEvent.setup()

    await user.type(screen.getByRole("textbox", { name: "Message Ada" }), "Fix the runtime{Enter}")

    expect(sent()).toEqual([
      { method: "thread.turn.start", params: { thread_id: "t1", message: "Fix the runtime" } },
    ])
    expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message Ada" }).value).toBe("")
  })

  it("turns Send into Stop while a turn runs, and Stop interrupts the turn", async () => {
    const events = thread(
      ["turn-1", started("Deploy it")],
      ["turn-1", { type: "turn.interrupted" }],
    )
    const { sent, subscription } = openThread({
      "thread.turn.interrupt": () => ({ sequence: 2, thread_id: "t1", turn_id: "turn-1" }),
    })
    await act(async () => {
      subscription().subscribed(0)
      subscription().deliver(events[0] as Event)
    })
    const user = userEvent.setup()

    expect(screen.queryByRole("button", { name: "Send" })).toBeNull()
    await user.click(screen.getByRole("button", { name: "Stop" }))
    expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])

    await act(async () => subscription().deliver(events[1] as Event))
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull()
    expect(screen.getByRole("button", { name: "Send" })).toBeDefined()
  })

  it("interrupts the running turn on Escape, unless Escape cancels an IME composition", async () => {
    const { sent, subscription } = openThread({
      "thread.turn.interrupt": () => ({ sequence: 2, thread_id: "t1", turn_id: "turn-1" }),
    })
    await act(async () => {
      subscription().subscribed(0)
      subscription().deliver(thread(["turn-1", started("Deploy it")])[0] as Event)
    })
    const field = screen.getByRole("textbox", { name: "Message Ada" })

    fireEvent.keyDown(field, { key: "Escape", isComposing: true })
    expect(sent()).toEqual([])

    fireEvent.keyDown(field, { key: "Escape" })
    expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])
  })

  it("says under the composer that the instance can make mistakes", async () => {
    const { subscription } = openThread()

    await act(async () => subscription().subscribed(0))

    expect(
      screen.getByText("Ada can make mistakes. It asks before anything it can't undo."),
    ).toBeDefined()
  })

  describe("the mode", () => {
    it("shows the thread's mode and disables the modes above the instance's ceiling", async () => {
      const { subscription } = openThread({ "thread.list": listing(undefined, "ask") })
      await act(async () => subscription().subscribed(0))
      const user = userEvent.setup()

      expect((await modePicker()).textContent).toContain("Ask")
      await user.click(await modePicker())
      await screen.findAllByRole("option")
      const option = (name: RegExp) => screen.getByRole("option", { name })
      const modes = [/^Read-only/, /^Ask/, /^Auto/, /^Full access/].map(option)

      expect(modes.map((mode) => mode.getAttribute("aria-disabled") === "true")).toEqual([
        false,
        false,
        true,
        true,
      ])
      expect(
        modes.map((mode) =>
          mode.textContent?.includes("Above the ceiling. Raise it in Configure."),
        ),
      ).toEqual([false, false, true, true])
    })

    it("pins the mode picked and shows it once the instance lists it", async () => {
      let mode: PermissionMode = "ask"
      const { sent, subscription } = openThread({
        "thread.list": listing(() => threadSummary({ mode, mode_pinned: mode !== "ask" })),
        "thread.mode.set": (params) => {
          mode = params.mode
          return { thread_id: "t1", turn_id: "pin", sequence: 1 }
        },
      })
      await act(async () => subscription().subscribed(0))
      const user = userEvent.setup()

      await user.click(await modePicker())
      await user.click(await screen.findByRole("option", { name: /^Read-only/ }))

      expect(sent()).toEqual([
        { method: "thread.mode.set", params: { thread_id: "t1", mode: "read-only" } },
      ])
      expect((await modePicker()).textContent).toContain("Read-only")
    })

    it("refuses another pick while the change is out, and shows why once it fails", async () => {
      const lister = instanceClient({ "thread.list": listing() })
      const sent: string[] = []
      let fail = (_error: CallError) => {}
      const client: typeof lister = {
        ...lister,
        call: (method, params) => {
          if (method === "thread.list") return lister.call(method, params)
          sent.push(method)
          return new Promise<never>((_resolve, reject) => (fail = reject))
        },
      }
      const { subscription } = openThread({}, client)
      await act(async () => subscription().subscribed(0))
      const picker = await modePicker()
      const user = userEvent.setup()

      act(() => picker.focus())
      await user.keyboard("{ArrowDown}")
      await screen.findAllByRole("option")
      await user.keyboard("{ArrowUp}{Enter}")

      expect(sent).toEqual(["thread.mode.set"])
      expect(picker.getAttribute("aria-readonly")).toBe("true")
      expect(screen.getByRole("status", { name: "Changing the mode" })).toBeDefined()

      await user.click(picker)
      await user.click(await screen.findByRole("option", { name: /^Full access/ }))

      expect(sent).toEqual(["thread.mode.set"])

      await act(async () =>
        fail(
          new CallError({
            code: "CONNECTION_LOST",
            message: "The connection to the instance dropped.",
            retryable: true,
          }),
        ),
      )

      expect(screen.getByRole("alert").textContent).toContain(
        "The connection to the instance dropped.",
      )
      expect(screen.queryByRole("status", { name: "Changing the mode" })).toBeNull()
      expect(picker.hasAttribute("aria-readonly")).toBe(false)
      expect(picker.textContent).toContain("Ask")
    })
  })
})
