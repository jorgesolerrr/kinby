import { stubCaller } from "@kinby/contract/testing"
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { hubClient, openThread, started, thread, traceLine } from "@/components/thread-testing"

describe("a waiting takeover", () => {
  const reason = "Sign in to LinkedIn: it asks for a code from your phone."
  const parked = thread(
    ["turn-1", started("Check LinkedIn")],
    [
      "turn-1",
      { type: "tool.call", call_id: "c1", name: "browser_takeover", arguments: { reason } },
    ],
    ["turn-1", { type: "takeover.requested", takeover_id: "k1", reason }],
  )
  const turn = { instance_id: "hub-ada", turn_id: "turn-1" }
  const takeover = { ...turn, thread_id: "t1", takeover_id: "k1" }
  const interrupted = () => ({ sequence: 4, thread_id: "t1", turn_id: "turn-1" })

  /**
   * Open the thread with its replay parked on takeover k1, and the live view's subscription on
   * the hub, which keeps the page.
   */
  async function openParked() {
    const hub = hubClient({
      "thread.takeover.input": () => ({}),
      "thread.takeover.finish": () => ({ sequence: 3, thread_id: "t1", turn_id: "turn-1" }),
    })
    const opened = openThread({ "thread.turn.interrupt": interrupted }, undefined, hub)
    const events = opened.subscription()
    await act(async () => {
      events.subscribed(parked.length)
      for (const event of parked) events.deliver(event)
    })
    const view = hub.subscriptions.find(
      (subscription) => subscription.method === "thread.takeover.start",
    )
    if (view === undefined) throw new Error("The panel did not start the live view.")
    const toHub = () => hub.calls.filter((call) => call.method.startsWith("thread.takeover"))
    return { ...opened, view, toHub }
  }

  const card = () => screen.getByRole("region", { name: "Take over the browser" })

  it("shows at the end of the parked turn with the reason, and starts the live view of the takeover", async () => {
    const { view } = await openParked()

    expect(
      traceLine("Using the browser").compareDocumentPosition(card()) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(card().textContent).toContain("Ada needs you in the browser")
    expect(card().textContent).toContain(reason)
    expect(view.params).toEqual(turn)
  })

  it("leaves the composer in place, with Stop in place of Send", async () => {
    await openParked()

    expect(screen.getByRole("textbox", { name: "Message Ada" })).toBeDefined()
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull()
    expect(screen.getByRole("button", { name: "Stop" })).toBeDefined()
  })

  it("shows each frame of the screencast", async () => {
    const { view } = await openParked()

    await act(async () => {
      view.subscribed(0)
      view.deliver({ data: "/9j/first" })
      view.deliver({ data: "/9j/second" })
    })

    expect(screen.getByRole<HTMLImageElement>("img", { name: "The browser's page" }).src).toBe(
      "data:image/jpeg;base64,/9j/second",
    )
  })

  it("sends a click at the page's own pixels, and the keys pressed on the live view", async () => {
    const { view, toHub } = await openParked()
    await act(async () => {
      view.subscribed(0)
      view.deliver({ data: "/9j/frame" })
    })
    const page = screen.getByRole<HTMLImageElement>("img", { name: "The browser's page" })
    // The live view shows a 1280 by 720 page at half its size.
    Object.defineProperties(page, {
      naturalWidth: { value: 1280 },
      naturalHeight: { value: 720 },
    })
    vi.spyOn(page, "getBoundingClientRect").mockReturnValue(new DOMRect(10, 20, 640, 360))
    const browser = screen.getByRole("application", { name: "The browser" })

    await act(async () => {
      fireEvent.mouseDown(page, { clientX: 110, clientY: 70 })
      fireEvent.mouseUp(page, { clientX: 110, clientY: 70 })
      fireEvent.keyDown(browser, { key: "a" })
      fireEvent.keyDown(browser, { key: "Shift" })
      fireEvent.keyDown(browser, { key: "Tab", shiftKey: true })
    })

    await waitFor(() =>
      expect(toHub().map((call) => call.params)).toEqual([
        { ...turn, input: { type: "pointer", action: "down", x: 200, y: 100 } },
        { ...turn, input: { type: "pointer", action: "up", x: 200, y: 100 } },
        { ...turn, input: { type: "key", key: "a" } },
        { ...turn, input: { type: "key", key: "Shift+Tab" } },
      ]),
    )
  })

  it("finishes only once the input sent before Finish reached the browser", async () => {
    let delivered = () => {}
    const answers = stubCaller({
      "thread.takeover.finish": () => ({ sequence: 3, thread_id: "t1", turn_id: "turn-1" }),
    })
    const hub = {
      ...hubClient(),
      calls: answers.calls,
      call: ((method, params) =>
        method === "thread.takeover.input"
          ? new Promise((resolve) => (delivered = () => resolve({})))
          : answers.call(method, params)) as typeof answers.call,
    }
    const { subscription } = openThread({}, undefined, hub)
    await act(async () => {
      subscription().subscribed(parked.length)
      for (const event of parked) subscription().deliver(event)
    })
    const finished = () => answers.calls.some((call) => call.method === "thread.takeover.finish")

    fireEvent.keyDown(screen.getByRole("application", { name: "The browser" }), { key: "7" })
    await userEvent.setup().click(screen.getByRole("button", { name: "Finish" }))
    expect(finished()).toBe(false)

    await act(async () => delivered())
    await waitFor(() => expect(finished()).toBe(true))
  })

  it("finishes the takeover, which resumes the turn", async () => {
    const { toHub } = await openParked()

    await userEvent.setup().click(screen.getByRole("button", { name: "Finish" }))

    expect(toHub()).toEqual([{ method: "thread.takeover.finish", params: takeover }])
  })

  it("stops the turn", async () => {
    const { sent } = await openParked()

    await userEvent.setup().click(within(card()).getByRole("button", { name: "Stop the turn" }))

    expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])
  })

  it("saves the browser as a profile by the name given, when finishing", async () => {
    const { toHub } = await openParked()
    const user = userEvent.setup()

    await user.type(screen.getByRole("textbox", { name: "Save as a profile" }), "LinkedIn")
    await user.click(screen.getByRole("button", { name: "Finish" }))

    expect(toHub()).toEqual([
      { method: "thread.takeover.finish", params: { ...takeover, profile: "LinkedIn" } },
    ])
  })
})
