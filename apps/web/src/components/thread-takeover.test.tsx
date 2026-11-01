import { stubCaller } from "@kinby/contract/testing"
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  hubClient,
  onAPhone,
  openThread,
  type Payload,
  started,
  thread,
  traceLine,
} from "@/components/thread-testing"

describe("a waiting takeover", () => {
  afterEach(() => window.history.replaceState(null, "", "/"))

  const reason = "Sign in to LinkedIn: it asks for a code from your phone."
  const browserOpen: Payload = {
    type: "tool.call",
    call_id: "c0",
    name: "browser_open",
    arguments: { url: "https://www.linkedin.com/login" },
  }
  /** Turn turn-1, parked on takeover k1 after doing `earlier`. */
  const parkedAfter = (...earlier: Payload[]) =>
    thread(
      ["turn-1", started("Check LinkedIn")],
      ...earlier.map((payload): [string, Payload] => ["turn-1", payload]),
      ["turn-1", browserOpen],
      [
        "turn-1",
        { type: "tool.call", call_id: "c1", name: "browser_takeover", arguments: { reason } },
      ],
      ["turn-1", { type: "takeover.requested", takeover_id: "k1", reason }],
    )
  const parked = parkedAfter()
  const turn = { instance_id: "hub-ada", turn_id: "turn-1" }
  const takeover = { ...turn, thread_id: "t1", takeover_id: "k1" }
  const interrupted = () => ({ sequence: 4, thread_id: "t1", turn_id: "turn-1" })

  /** Open the thread with its replay parked on takeover k1. */
  async function openParked(events = parked) {
    const hub = hubClient({
      "thread.takeover.input": () => ({}),
      "thread.takeover.finish": () => ({ sequence: 3, thread_id: "t1", turn_id: "turn-1" }),
    })
    const panel = openThread({ "thread.turn.interrupt": interrupted }, undefined, hub)
    await act(async () => {
      panel.subscription().subscribed(events.length)
      for (const event of events) panel.subscription().deliver(event)
    })
    const liveView = () =>
      hub.subscriptions.find((subscription) => subscription.method === "thread.takeover.start")
    const toHub = () => hub.calls.filter((call) => call.method.startsWith("thread.takeover"))
    /** What the turn does next, which ends its wait on the takeover. */
    const moveOn = (payload: Payload) =>
      act(async () =>
        panel.subscription().deliver({
          sequence: events.length + 1,
          thread_id: "t1",
          turn_id: "turn-1",
          timestamp: "2026-09-28T10:00:00Z",
          payload,
        }),
      )
    return { ...panel, liveView, toHub, moveOn }
  }

  /**
   * Open the parked thread with its takeover's pane, and the live view's subscription on the hub,
   * which keeps the page.
   */
  async function openInPane(events = parked) {
    const opened = await openParked(events)
    await userEvent.setup().click(within(card()).getByRole("button", { name: "Open linkedin.com" }))
    const view = opened.liveView()
    if (view === undefined) throw new Error("The pane did not start the live view.")
    return { ...opened, view }
  }

  const card = () => screen.getByRole("region", { name: "Take over the browser" })
  const pane = () => screen.queryByRole("region", { name: "linkedin.com" })
  const openPane = () => {
    const open = pane()
    if (open === null) throw new Error("The takeover's pane is not open.")
    return open
  }

  it("shows a card at the end of the parked turn with the reason, and no live browser", async () => {
    const { liveView } = await openParked()

    expect(
      traceLine("Opening").compareDocumentPosition(card()) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(card().textContent).toContain("Ada needs you in the browser")
    expect(card().textContent).toContain(reason)
    expect(within(card()).getByRole("button", { name: "Open linkedin.com" })).toBeDefined()
    expect(within(card()).getByRole("button", { name: "Stop the turn" })).toBeDefined()
    expect(screen.queryByRole("application", { name: "The browser" })).toBeNull()
    expect(liveView()).toBeUndefined()
  })

  it("names no site when the turn opened none", async () => {
    await openParked(parked.filter((event) => event.payload !== browserOpen))

    expect(within(card()).getByRole("button", { name: "Open the browser" })).toBeDefined()
  })

  it("opens the browser's pane beside the thread, with the live view of the takeover", async () => {
    const { view } = await openInPane()

    expect(openPane().textContent).toContain("Ada is waiting on you")
    expect(openPane().textContent).toContain(reason)
    expect(within(openPane()).getByRole("application", { name: "The browser" })).toBeDefined()
    expect(view.params).toEqual(turn)
    expect(within(card()).getByRole("button", { name: "Show the browser" })).toBeDefined()
    expect(new URLSearchParams(window.location.search).get("pane")).toBe("takeover")
  })

  it("closes the pane and leaves the takeover waiting, with the card offering the site again", async () => {
    const { view, toHub } = await openInPane()

    await userEvent.setup().click(within(openPane()).getByRole("button", { name: "Close" }))

    expect(pane()).toBeNull()
    expect(view.cancelled).toBe(true)
    expect(toHub()).toEqual([])
    expect(within(card()).getByRole("button", { name: "Open linkedin.com" })).toBeDefined()
    expect(window.location.search).toBe("")
  })

  it("closes the pane once the takeover ends", async () => {
    const { moveOn } = await openInPane()

    await moveOn({ type: "message.delta", text: "Signed in." })

    expect(pane()).toBeNull()
    expect(screen.queryByRole("region", { name: "Take over the browser" })).toBeNull()
    expect(window.location.search).toBe("")
  })

  it("shows the pane when the thread opens with it in the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/threads/t1?pane=takeover")

    const { liveView } = await openParked()

    expect(openPane()).toBeDefined()
    expect(liveView()?.params).toEqual(turn)
  })

  it("opens nothing from the URL when the turn is not parked on a takeover", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/threads/t1?pane=takeover")

    await openParked(parked.slice(0, 2))

    expect(pane()).toBeNull()
    expect(window.location.search).toBe("")
  })

  it("gives the pane to a view opened from the thread, and the takeover still waits", async () => {
    const wrote: Payload = {
      type: "view.changed",
      view: "applications",
      title: "Job board",
      change: "changed",
    }
    const { view, toHub } = await openInPane(parkedAfter(wrote))

    await userEvent.setup().click(screen.getByRole("link", { name: "Changed the view Job board" }))

    expect(pane()).toBeNull()
    expect(view.cancelled).toBe(true)
    expect(toHub()).toEqual([])
    expect(within(card()).getByRole("button", { name: "Open linkedin.com" })).toBeDefined()
    expect(new URLSearchParams(window.location.search).get("pane")).toBe("view.applications")
  })

  it("hides the thread beside the pane, and the same button brings it back", async () => {
    await openInPane()
    const user = userEvent.setup()

    await user.click(within(openPane()).getByRole("button", { name: "Hide the thread" }))
    expect(screen.queryByRole("textbox", { name: "Message Ada" })).toBeNull()

    await user.click(within(openPane()).getByRole("button", { name: "Show the thread" }))
    expect(screen.getByRole("textbox", { name: "Message Ada" })).toBeDefined()
  })

  describe("on a phone", () => {
    onAPhone()

    const drawer = () => screen.queryByRole("dialog", { name: "linkedin.com" })

    it("opens the browser in a drawer over the thread, with the pane's heading", async () => {
      await openInPane()

      const opened = drawer()
      if (opened === null) throw new Error("The drawer is not open.")
      expect(opened.textContent).toContain("Ada is waiting on you")
      expect(opened.textContent).toContain(reason)
      expect(within(opened).getByRole("application", { name: "The browser" })).toBeDefined()
      expect(within(opened).queryByRole("button", { name: "Hide the thread" })).toBeNull()
      expect(pane()).toBeNull()
    })

    it("closes the drawer and leaves the takeover waiting", async () => {
      const { view, toHub } = await openInPane()
      const opened = drawer()
      if (opened === null) throw new Error("The drawer is not open.")

      await userEvent.setup().click(within(opened).getByRole("button", { name: "Close" }))

      await waitFor(() => expect(drawer()).toBeNull())
      expect(view.cancelled).toBe(true)
      expect(toHub()).toEqual([])
      expect(within(card()).getByRole("button", { name: "Open linkedin.com" })).toBeDefined()
      expect(window.location.search).toBe("")
    })
  })

  it("keeps the composer's draft when the screen narrows to a phone's", async () => {
    const wide = window.matchMedia
    const phone = Object.assign(wide("(max-width: 767px)"), { matches: false })
    window.matchMedia = (query) => (query === phone.media ? phone : wide(query))
    try {
      await openInPane()
      fireEvent.change(screen.getByRole("textbox", { name: "Message Ada" }), {
        target: { value: "Half a thought" },
      })

      act(() => {
        phone.matches = true
        phone.dispatchEvent(new Event("change"))
      })

      expect(screen.getByRole("dialog", { name: "linkedin.com" })).toBeDefined()
      // The drawer is modal, so the thread behind it is hidden from the accessibility tree.
      expect(
        screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message Ada", hidden: true })
          .value,
      ).toBe("Half a thought")
    } finally {
      window.matchMedia = wide
    }
  })

  it("leaves the composer in place, with Stop in place of Send", async () => {
    await openParked()

    expect(screen.getByRole("textbox", { name: "Message Ada" })).toBeDefined()
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull()
    expect(screen.getByRole("button", { name: "Stop" })).toBeDefined()
  })

  it("shows each frame of the screencast", async () => {
    const { view } = await openInPane()

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
    const { view, toHub } = await openInPane()
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

  it("scrolls the page under the live view, and not the pane around it", async () => {
    const { toHub } = await openInPane()

    const scrolled = fireEvent.wheel(screen.getByRole("application", { name: "The browser" }), {
      deltaY: 120,
    })

    expect(scrolled).toBe(false)
    await waitFor(() =>
      expect(toHub().map((call) => call.params)).toEqual([
        { ...turn, input: { type: "wheel", delta_x: 0, delta_y: 120 } },
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
    window.history.replaceState(null, "", "/instances/hub-ada/threads/t1?pane=takeover")
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
    const { toHub } = await openInPane()

    await userEvent.setup().click(within(openPane()).getByRole("button", { name: "Finish" }))

    expect(toHub()).toEqual([{ method: "thread.takeover.finish", params: takeover }])
  })

  it("stops the turn from the card", async () => {
    const { sent } = await openParked()

    await userEvent.setup().click(within(card()).getByRole("button", { name: "Stop the turn" }))

    expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])
  })

  it("stops the turn from the pane", async () => {
    const { sent } = await openInPane()

    await userEvent.setup().click(within(openPane()).getByRole("button", { name: "Stop the turn" }))

    expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])
  })

  it("saves the browser as a profile by the name given, when finishing", async () => {
    const { toHub } = await openInPane()
    const user = userEvent.setup()

    // Nothing in jsdom has a size, so the split's handle sits under every press and takes its
    // focus. The field takes it from the keyboard instead.
    act(() => screen.getByRole("textbox", { name: "Save as a profile" }).focus())
    await user.keyboard("LinkedIn")
    await user.click(screen.getByRole("button", { name: "Finish" }))

    expect(toHub()).toEqual([
      { method: "thread.takeover.finish", params: { ...takeover, profile: "LinkedIn" } },
    ])
  })
})
