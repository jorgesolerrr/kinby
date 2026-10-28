import {
  CallError,
  type Event,
  type Notification,
  type NotificationRaised,
  type ViewChange,
} from "@kinby/contract"
import { type Answers, fakeClock, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest"

import { ThreadPanel } from "@/components/thread-panel"
import { threadList } from "@/lib/thread-list"

type Payload = Event["payload"]

/** A client of an instance whose socket is up, answering from `answers`. */
function instanceClient(answers: Answers = {}) {
  return {
    ...stubCaller(answers),
    ...stubSubscriber(),
    state: () => "connected" as const,
    onStateChange: () => () => {},
  }
}

/** A client of the hub, answering from `answers`. */
function hubClient(answers: Answers = {}) {
  return { ...stubCaller(answers), ...stubSubscriber() }
}

/**
 * Open thread t1 of Ada's instance, from a client that answers from `answers`, with a hub that
 * answers from `hub`, on `clock`.
 */
function openThread(
  answers: Answers = {},
  client = instanceClient(answers),
  hub = hubClient(),
  clock = fakeClock(),
) {
  const rendered = render(
    <ThreadPanel
      client={client}
      hub={hub}
      clock={clock}
      instanceId="hub-ada"
      threadId="t1"
      name="Ada"
      avatar={{ shape: "circle", color: "blue" }}
    />,
  )
  const subscription = () => {
    const latest = client.subscriptions.at(-1)
    if (latest === undefined) throw new Error("The panel did not subscribe.")
    return latest
  }
  /** What the panel called besides the header listing the threads. */
  const sent = () => client.calls.filter((call) => call.method !== "thread.list")
  return { client, sent, subscription, unmount: rendered.unmount }
}

/** The thread's events, numbered from 1 in the order given. */
function thread(...events: [turnId: string, payload: Payload][]): Event[] {
  return events.map(([turnId, payload], index) => ({
    sequence: index + 1,
    thread_id: "t1",
    turn_id: turnId,
    timestamp: "2026-09-28T10:00:00Z",
    payload,
  }))
}

const started = (message: string): Payload => ({
  type: "turn.started",
  message,
  model: "claude",
  origin: { kind: "user" },
})

const loading = () => screen.queryByRole("status", { name: "Loading the thread" })

describe("a thread's panel", () => {
  it("follows the thread after the sequence its store has, loading until the replay reaches the head", async () => {
    const events = thread(
      ["turn-1", started("Fix the runtime")],
      ["turn-1", { type: "message.delta", text: "On it." }],
    )
    const { client, subscription, unmount } = openThread()

    expect(subscription().params).toEqual({ thread_id: "t1", after_sequence: 0 })
    expect(loading()).not.toBeNull()

    await act(async () => {
      subscription().subscribed(2)
      subscription().deliver(events[0] as Event)
    })
    expect(loading()).not.toBeNull()

    await act(async () => subscription().deliver(events[1] as Event))
    expect(loading()).toBeNull()
    expect(screen.getByText("Fix the runtime")).toBeDefined()
    expect(screen.getByText("On it.")).toBeDefined()

    unmount()
    expect(subscription().cancelled).toBe(true)
    openThread({}, client)
    expect(subscription().params).toEqual({ thread_id: "t1", after_sequence: 2 })
  })

  it("marks who started each turn, each tool call's main argument and gate decision, and how the turn ended", async () => {
    const gated = (
      callId: string,
      name: string,
      action: "allow" | "deny",
      decidedBy: "policy" | "user",
      rule: string,
    ): Payload => ({
      type: "tool.gated",
      call_id: callId,
      name,
      action,
      decided_by: decidedBy,
      rule,
    })
    const call = (callId: string, name: string, args: Record<string, string>): Payload => ({
      type: "tool.call",
      call_id: callId,
      name,
      arguments: args,
    })
    const events = thread(
      ["turn-1", started("Fix the runtime")],
      ["turn-1", call("c1", "read", { path: "runtime.py" })],
      ["turn-1", call("c2", "write", { path: "notes.md", content: "…" })],
      ["turn-1", gated("c1", "read", "allow", "policy", "reads are allowed")],
      ["turn-1", gated("c2", "write", "deny", "policy", "read-only mode denies writes")],
      [
        "turn-1",
        {
          type: "tool.result",
          call_id: "c1",
          name: "read",
          output: "",
          error: false,
          duration_ms: 38,
        },
      ],
      ["turn-1", call("c3", "edit", { path: "runtime.py", old: "a", new: "b" })],
      ["turn-1", gated("c3", "edit", "allow", "user", "ask mode asks before writes")],
      ["turn-1", { type: "turn.completed", input_tokens: 1_200, output_tokens: 300 }],
      [
        "turn-2",
        {
          type: "turn.started",
          message: "Summarize the night's email",
          model: "claude",
          origin: { kind: "routine", name: "nightly-digest", trigger: "scheduled" },
        },
      ],
      [
        "turn-2",
        { type: "turn.failed", code: "BUDGET_EXCEEDED", message: "The steps budget ran out." },
      ],
      ["turn-3", started("Deploy it")],
      ["turn-3", { type: "turn.interrupted" }],
      ["turn-4", started("Deploy it now")],
      ["turn-4", call("c5", "read", { path: "deploy.log" })],
      ["turn-4", call("c4", "bash", { command: "make deploy" })],
      [
        "turn-4",
        {
          type: "approval.requested",
          approval_id: "a1",
          name: "bash",
          arguments: { command: "make deploy" },
          rule: "ask mode asks before commands",
        },
      ],
    )
    const { subscription } = openThread()

    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    for (const marker of [
      "read runtime.py · 38 ms",
      "write notes.md · denied by policy: read-only mode denies writes",
      "edit runtime.py · approved by you · running",
      "Done · 3 steps · 1,500 tokens",
      "Failed: The steps budget ran out. (BUDGET_EXCEEDED). Send a message to try again.",
      "Stopped",
      "read deploy.log · running",
      "bash make deploy · waiting for you",
      "Working",
    ]) {
      expect(screen.getByText(marker)).toBeDefined()
    }
    expect(screen.getAllByText(/^You ·/)).toHaveLength(3)
    expect(screen.getByText(/^Routine nightly-digest ·/)).toBeDefined()
  })

  it("marks a turn the home instance started", async () => {
    const events = thread([
      "turn-1",
      {
        type: "turn.started",
        message: "Check my job applications.",
        model: "claude",
        origin: { kind: "home-instance" },
      },
    ])
    const { subscription } = openThread()

    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    expect(screen.getByText(/^Home instance ·/)).toBeDefined()
    expect(screen.queryByText(/^You ·/)).toBeNull()
  })

  it("names the browser's zone in the time a turn started", async () => {
    vi.stubEnv("TZ", "America/Bogota")
    onTestFinished(() => void vi.unstubAllEnvs())
    const events = thread(["turn-1", started("Fix the runtime")])
    const { subscription } = openThread()

    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    const bogota = new Intl.DateTimeFormat("en", {
      timeZone: "America/Bogota",
      timeZoneName: "short",
    })
      .formatToParts(new Date("2026-09-28T10:00:00Z"))
      .find((part) => part.type === "timeZoneName")?.value
    if (bogota === undefined) throw new Error("America/Bogota has no short zone name")
    const time = screen.getByText(/^You ·/).querySelector("time")?.textContent ?? ""
    expect(time).toContain(bogota)
  })

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

  describe("a turn the URL names", () => {
    const turns = ["turn-1", "turn-2", "turn-3"]

    // jsdom lays nothing out and cannot scroll. Here each turn stands 1,000 px tall below the one
    // before it, scrolling the transcript moves them up, and the thread opens on the last at 2,000.
    beforeEach(() => {
      vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
        this: Element,
      ) {
        const index = turns.indexOf(this.getAttribute("data-message-id") ?? "")
        if (index === -1) return DOMRect.fromRect()
        const scrolled = this.closest("[data-slot=message-scroller-viewport]")?.scrollTop ?? 0
        return DOMRect.fromRect({ y: index * 1_000 - scrolled, height: 1_000 })
      })
      Object.defineProperty(Element.prototype, "scrollTo", {
        configurable: true,
        value(this: Element, { top = 0 }: ScrollToOptions) {
          this.scrollTop = top
        },
      })
    })

    afterEach(() => {
      vi.restoreAllMocks()
      Reflect.deleteProperty(Element.prototype, "scrollTo")
    })

    /** Open the thread at `path` and replay its three turns, then say where the transcript sits. */
    async function scrolledAt(path: string) {
      window.history.replaceState(null, "", path)
      const { subscription } = openThread()
      const events = thread(...turns.map((turn): [string, Payload] => [turn, started(turn)]))
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      return screen.getByRole("region", { name: "Messages" }).scrollTop
    }

    it("opens the thread scrolled to that turn, not the latest", async () => {
      expect(await scrolledAt("/instances/hub-ada/threads/t1/turns/turn-2")).toBe(1_000)
    })

    it("opens the thread on its latest turn when the thread has no such turn", async () => {
      expect(await scrolledAt("/instances/hub-ada/threads/t1/turns/turn-9")).toBe(2_000)
    })

    it("leaves a thread opened without a turn on its latest turn", async () => {
      expect(await scrolledAt("/instances/hub-ada/threads/t1")).toBe(2_000)
    })

    it("scrolls to the turn again when Back returns to it from the plain thread", async () => {
      await scrolledAt("/instances/hub-ada/threads/t1/turns/turn-2")
      const transcript = screen.getByRole("region", { name: "Messages" })

      act(() => {
        window.history.pushState(null, "", "/instances/hub-ada/threads/t1")
        window.dispatchEvent(new PopStateEvent("popstate"))
      })
      transcript.scrollTop = 0
      act(() => {
        window.history.pushState(null, "", "/instances/hub-ada/threads/t1/turns/turn-2")
        window.dispatchEvent(new PopStateEvent("popstate"))
      })

      await waitFor(() => expect(transcript.scrollTop).toBe(1_000))
    })
  })

  it("puts focus in the composer once the replay has loaded", async () => {
    const { subscription } = openThread()

    await act(async () => subscription().subscribed(0))

    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Message Ada" }))
  })

  it("puts focus back in the composer when the field around it is clicked", async () => {
    const { subscription } = openThread()
    await act(async () => subscription().subscribed(0))
    const composer = screen.getByRole("textbox", { name: "Message Ada" })
    composer.blur()

    fireEvent.click(screen.getByText("›"))

    expect(document.activeElement).toBe(composer)
  })

  it("lines up the instance's name the same in a turn of only tool steps as in one with text", async () => {
    const events = thread(
      ["turn-1", started("List the repo")],
      ["turn-1", { type: "tool.call", call_id: "c1", name: "bash", arguments: { command: "ls" } }],
      ["turn-2", started("Say hi")],
      ["turn-2", { type: "message.delta", text: "Hi." }],
    )
    const { subscription } = openThread()
    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })
    // jsdom has no Tailwind, so compare the classes the two names are drawn with.
    const [toolsOnly, withText] = screen.getAllByText("Ada")

    expect(toolsOnly?.className).toBe(withText?.className)
    expect(toolsOnly?.parentElement?.className).toBe(withText?.parentElement?.className)
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

  describe("a waiting approval", () => {
    const parked = thread(
      ["turn-1", started("Deploy it")],
      [
        "turn-1",
        { type: "tool.call", call_id: "c1", name: "bash", arguments: { command: "make deploy" } },
      ],
      [
        "turn-1",
        {
          type: "approval.requested",
          approval_id: "a1",
          name: "bash",
          arguments: { command: "make deploy" },
          rule: "mode.ask.write",
        },
      ],
    )
    const accepted = () => ({ sequence: 3, thread_id: "t1", turn_id: "turn-1" })

    /** Reopen the thread with its replay parked on approval a1. */
    async function reopenParked(answers: Answers = {}) {
      const opened = openThread({
        "thread.approval.respond": accepted,
        "thread.turn.interrupt": accepted,
        ...answers,
      })
      await act(async () => {
        opened.subscription().subscribed(parked.length)
        for (const event of parked) opened.subscription().deliver(event)
      })
      return opened
    }

    it("marks the answer pressed while it is out, and shows why once it fails", async () => {
      let fail = (_error: CallError) => {}
      const { subscription } = openThread(
        {},
        {
          ...instanceClient(),
          call: () => new Promise<never>((_resolve, reject) => (fail = reject)),
        },
      )
      await act(async () => {
        subscription().subscribed(parked.length)
        for (const event of parked) subscription().deliver(event)
      })
      const button = (name: string) =>
        screen.getByRole<HTMLButtonElement>("button", { name: new RegExp(name) })

      await userEvent.setup().click(button("Approve"))

      expect(["Approve", "Deny", "Stop the turn"].map((name) => button(name).disabled)).toEqual([
        true,
        true,
        true,
      ])
      expect(within(button("Approve")).getByRole("status")).toBeDefined()
      expect(within(button("Deny")).queryByRole("status")).toBeNull()

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
      expect(within(button("Approve")).queryByRole("status")).toBeNull()
      expect(button("Approve")).toHaveProperty("disabled", false)
    })

    it("takes the composer's place with the tool, its main argument, the rule, and the arguments", async () => {
      await reopenParked()
      const approval = screen.getByRole("region", { name: "Approve bash make deploy?" })

      expect(screen.queryByRole("textbox", { name: "Message Ada" })).toBeNull()
      expect(screen.queryByRole("button", { name: "Send" })).toBeNull()
      expect(approval.textContent).toContain("mode.ask.write")
      expect(approval.textContent).toContain('"command": "make deploy"')
    })

    it("puts focus in the reason when the thread opens on it", async () => {
      await reopenParked()

      expect(document.activeElement).toBe(
        screen.getByRole("textbox", { name: "Reason to deny (optional)" }),
      )
    })

    /** The instance's thread list, so the header shows t1's title. */
    const listed: Answers = {
      "thread.list": () => ({
        threads: [
          {
            id: "t1",
            title: "Deploy notes",
            created_at: "2026-09-28T10:00:00Z",
            last_activity_at: "2026-09-28T10:00:00Z",
            status: "running",
            mode: "ask",
            mode_pinned: false,
            archived: false,
            origin: { kind: "user" },
          },
        ],
        ceiling: "full-access",
        cursor: null,
      }),
    }

    /**
     * Open the thread with its turn running and nothing parked, do what the test does before, and
     * then park approval a1 on the turn.
     */
    async function parkLive(before: () => Promise<void> = async () => {}) {
      const opened = openThread({ ...listed, "thread.approval.respond": accepted })
      const [start, ...rest] = parked
      await act(async () => {
        opened.subscription().subscribed(1)
        opened.subscription().deliver(start as Event)
      })
      await before()
      await act(async () => {
        for (const event of rest) opened.subscription().deliver(event)
      })
      return opened
    }

    it("takes focus from the composer into the panel itself, not a field or button in it", async () => {
      const { sent } = await parkLive(async () => {
        expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Message Ada" }))
      })
      const user = userEvent.setup()

      expect(document.activeElement).toBe(
        screen.getByRole("region", { name: "Approve bash make deploy?" }),
      )

      await user.keyboard("x{Enter}")
      expect(sent()).toEqual([])
      expect(screen.getByRole("textbox", { name: "Reason to deny (optional)" })).toHaveProperty(
        "value",
        "",
      )

      await user.tab()
      expect(document.activeElement).toBe(
        screen.getByRole("textbox", { name: "Reason to deny (optional)" }),
      )
      await user.tab()
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Approve" }))
    })

    it("leaves focus in the title being renamed when it parks", async () => {
      await parkLive(async () => {
        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Deploy notes" }))
        await user.type(screen.getByRole("textbox", { name: "Thread title" }), " for staging")
      })

      expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Thread title" }))
    })

    it("approves it", async () => {
      const { sent } = await reopenParked()

      await userEvent.setup().click(screen.getByRole("button", { name: "Approve" }))

      expect(sent()).toEqual([
        {
          method: "thread.approval.respond",
          params: { thread_id: "t1", approval_id: "a1", decision: "approve" },
        },
      ])
    })

    it("does not offer to always allow it on a turn the user started", async () => {
      await reopenParked()

      expect(screen.queryByRole("button", { name: "Always allow in this routine" })).toBeNull()
    })

    it("always allows it in the routine whose turn parked on it", async () => {
      const opened = openThread({ "thread.approval.respond": accepted })
      const [, ...rest] = parked
      const routineStarted = thread([
        "turn-1",
        {
          type: "turn.started",
          message: "Post the news",
          model: "claude",
          origin: { kind: "routine", name: "news", trigger: "scheduled" },
        },
      ])
      await act(async () => {
        opened.subscription().subscribed(parked.length)
        for (const event of [...routineStarted, ...rest]) opened.subscription().deliver(event)
      })

      await userEvent
        .setup()
        .click(screen.getByRole("button", { name: "Always allow in this routine" }))

      expect(opened.sent()).toEqual([
        {
          method: "thread.approval.respond",
          params: { thread_id: "t1", approval_id: "a1", decision: "allow_in_routine" },
        },
      ])
    })

    /** Open a thread parked on approval a1 of a call to the authored tool echo. */
    async function reopenAuthored(preview: string | null = null) {
      const opened = openThread({ "thread.approval.respond": accepted })
      const authored = thread(
        ["turn-1", started("Echo it")],
        ["turn-1", { type: "tool.call", call_id: "c1", name: "echo", arguments: { text: "hi" } }],
        [
          "turn-1",
          {
            type: "approval.requested",
            approval_id: "a1",
            name: "echo",
            arguments: { text: "hi" },
            rule: "authored.write",
            preview,
          },
        ],
      )
      await act(async () => {
        opened.subscription().subscribed(authored.length)
        for (const event of authored) opened.subscription().deliver(event)
      })
      return opened
    }

    it("does not offer to mark a tool read-only unless the agent authored it", async () => {
      await reopenParked()

      expect(screen.queryByRole("button", { name: "Mark read-only" })).toBeNull()
    })

    it("marks the authored tool that asked read-only", async () => {
      const { sent } = await reopenAuthored()

      await userEvent.setup().click(screen.getByRole("button", { name: "Mark read-only" }))

      expect(sent()).toEqual([
        {
          method: "thread.approval.respond",
          params: { thread_id: "t1", approval_id: "a1", decision: "mark_read_only" },
        },
      ])
    })

    it("shows what the approval previews beside the arguments", async () => {
      await reopenAuthored("echo declares write=False.")

      expect(screen.getByText("echo declares write=False.")).toBeDefined()
    })

    it("denies it with the reason typed", async () => {
      const { sent } = await reopenParked()
      const user = userEvent.setup()

      await user.type(
        screen.getByRole("textbox", { name: "Reason to deny (optional)" }),
        "  Use staging instead ",
      )
      await user.click(screen.getByRole("button", { name: "Deny" }))

      expect(sent()).toEqual([
        {
          method: "thread.approval.respond",
          params: {
            thread_id: "t1",
            approval_id: "a1",
            decision: "deny",
            reason: "Use staging instead",
          },
        },
      ])
    })

    it("denies it with the reason typed when Enter is pressed, once", async () => {
      const { sent } = await reopenParked()

      await userEvent
        .setup()
        .type(
          screen.getByRole("textbox", { name: "Reason to deny (optional)" }),
          "Use staging instead{Enter}{Enter}",
        )

      expect(sent()).toEqual([
        {
          method: "thread.approval.respond",
          params: {
            thread_id: "t1",
            approval_id: "a1",
            decision: "deny",
            reason: "Use staging instead",
          },
        },
      ])
    })

    it("does not deny while an input method is composing the reason", async () => {
      const { sent } = await reopenParked()

      fireEvent.keyDown(screen.getByRole("textbox", { name: "Reason to deny (optional)" }), {
        key: "Enter",
        isComposing: true,
      })

      expect(sent()).toEqual([])
    })

    it("denies it without a reason when none is typed", async () => {
      const { sent } = await reopenParked()

      await userEvent.setup().click(screen.getByRole("button", { name: "Deny" }))

      expect(sent()).toEqual([
        {
          method: "thread.approval.respond",
          params: { thread_id: "t1", approval_id: "a1", decision: "deny" },
        },
      ])
    })

    it("stops the turn instead", async () => {
      const { sent } = await reopenParked()

      await userEvent.setup().click(screen.getByRole("button", { name: "Stop the turn" }))

      expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])
    })

    it("marks the call as not run when the turn stops, live and on replay", async () => {
      const { subscription, unmount } = await reopenParked()
      const stopped: Event = {
        sequence: 4,
        thread_id: "t1",
        turn_id: "turn-1",
        timestamp: "2026-09-28T10:00:00Z",
        payload: { type: "turn.interrupted" },
      }

      await act(async () => subscription().deliver(stopped))
      expect(screen.getByText("bash make deploy · not run, turn stopped")).toBeDefined()

      unmount()
      const reopened = openThread()
      await act(async () => {
        reopened.subscription().subscribed(stopped.sequence)
        for (const event of [...parked, stopped]) reopened.subscription().deliver(event)
      })
      expect(screen.getByText("bash make deploy · not run, turn stopped")).toBeDefined()
    })

    it("marks the call as not run when the turn fails", async () => {
      const { subscription } = await reopenParked()

      await act(async () =>
        subscription().deliver({
          sequence: 4,
          thread_id: "t1",
          turn_id: "turn-1",
          timestamp: "2026-09-28T10:00:00Z",
          payload: { type: "turn.failed", code: "INTERNAL", message: "The runner crashed." },
        }),
      )

      expect(screen.getByText("bash make deploy · not run, turn failed")).toBeDefined()
    })

    /** The event that moves the turn on once the user denied a1 for `reason`. */
    const denied = (reason: string): Event => ({
      sequence: 4,
      thread_id: "t1",
      turn_id: "turn-1",
      timestamp: "2026-09-28T10:00:00Z",
      payload: {
        type: "tool.gated",
        call_id: "c1",
        name: "bash",
        action: "deny",
        decided_by: "user",
        rule: "mode.ask.write",
        reason,
      },
    })

    it("gives the composer back once the turn moves on", async () => {
      const { subscription } = await reopenParked()

      await act(async () => subscription().deliver(denied("Use staging instead")))

      expect(screen.queryByRole("region", { name: "Approve bash make deploy?" })).toBeNull()
      expect(screen.getByRole("textbox", { name: "Message Ada" })).toBeDefined()
      expect(
        screen.getByText("bash make deploy · denied by you: Use staging instead"),
      ).toBeDefined()
    })

    it("returns focus to the composer once the answered approval clears", async () => {
      const { subscription } = await reopenParked()

      await userEvent.setup().click(screen.getByRole("button", { name: "Deny" }))
      await act(async () => subscription().deliver(denied("")))

      expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Message Ada" }))
    })

    it("leaves focus in the title being renamed when the approval clears", async () => {
      const { client, subscription } = await reopenParked(listed)
      await act(() => threadList(client, "sidebar").list())
      const user = userEvent.setup()

      await user.click(screen.getByRole("button", { name: "Deploy notes" }))
      await user.type(screen.getByRole("textbox", { name: "Thread title" }), " for staging")
      await act(async () => subscription().deliver(denied("")))

      expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Thread title" }))
    })
  })

  describe("a reply", () => {
    /** Open a thread whose one turn asked `request` and was answered with `reply`. */
    async function replied(request: string, reply: string) {
      const events = thread(
        ["turn-1", started(request)],
        ["turn-1", { type: "message.delta", text: reply }],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      return screen.getByText("Ada").parentElement as HTMLElement
    }

    it("renders its Markdown", async () => {
      const reply = await replied("Explain it", "The **runtime** reads `config.toml` first.")

      expect(reply.querySelector("strong")?.textContent).toBe("runtime")
      expect(reply.querySelector("code")?.textContent).toBe("config.toml")
      expect(reply.textContent).not.toContain("**")
      expect(reply.textContent).not.toContain("`")
    })

    it("renders a reply cut off mid-emphasis without the open marker", async () => {
      const reply = await replied("Explain it", "The **runtime reads")

      expect(reply.querySelector("strong")?.textContent).toBe("runtime reads")
      expect(reply.textContent).not.toContain("**")
    })

    it("renders a reply cut off inside a code fence without the fence", async () => {
      const reply = await replied("Show it", "Run this:\n\n```sh\nmake deploy")

      expect(reply.querySelector("pre")?.textContent).toContain("make deploy")
      expect(reply.textContent).not.toContain("```")
    })

    it("highlights the syntax of a fenced code block", async () => {
      const reply = await replied("Show it", "```python\nimport os\n```")

      await waitFor(() => {
        const keyword = [...reply.querySelectorAll("pre span")].find(
          (token) => token.textContent === "import",
        )
        expect(keyword?.getAttribute("style")).toContain("--sdm-c")
      })
    })

    it("leaves the request as typed", async () => {
      await replied("Deploy **now**, not `later`", "Deploying.")

      expect(screen.getByText("Deploy **now**, not `later`")).toBeDefined()
    })

    it("shows raw HTML as text", async () => {
      const reply = await replied("Explain it", "Use <b>bold</b> sparingly.")

      expect(reply.querySelector("b")).toBeNull()
      expect(reply.textContent).toContain("Use <b>bold</b> sparingly.")
    })

    it("loads no image it links to", async () => {
      const reply = await replied("Explain it", "Done. ![pixel](https://evil.example/?q=secret)")

      expect(reply.querySelector("img")).toBeNull()
      expect(reply.textContent).toContain("Done.")
    })

    it("opens its links in a new tab", async () => {
      await replied("Where is it?", "See [the docs](https://kinby.dev/docs).")

      const link = screen.getByRole("link", { name: "the docs" })
      expect(link.getAttribute("href")).toBe("https://kinby.dev/docs")
      expect(link.getAttribute("target")).toBe("_blank")
    })

    describe("a link to a page of the app", () => {
      const page = "/instances/hub-bo/threads/t9"

      beforeEach(() => window.history.replaceState(null, "", "/instances/hub-ada/threads/t1"))

      it("opens the page in place, as a new history entry", async () => {
        await replied("Ask Bo", `Bo is on it in [the thread](${page}).`)
        const link = screen.getByRole("link", { name: "the thread" })
        const entries = window.history.length

        await userEvent.click(link)

        expect(link.getAttribute("href")).toBe(page)
        expect(link.getAttribute("target")).toBeNull()
        expect(window.location.pathname).toBe(page)
        expect(window.history.length).toBe(entries + 1)
      })

      it("leaves a modified click to the browser", async () => {
        await replied("Ask Bo", `Bo is on it in [the thread](${page}).`)
        const link = screen.getByRole("link", { name: "the thread" })

        // jsdom cannot follow the link, so the window drops the click once the app has passed it on.
        const prevented: boolean[] = []
        const drop = (event: MouseEvent) => {
          prevented.push(event.defaultPrevented)
          event.preventDefault()
        }
        window.addEventListener("click", drop)
        onTestFinished(() => window.removeEventListener("click", drop))

        fireEvent.click(link, { ctrlKey: true })

        expect(prevented).toEqual([false])
        expect(window.location.pathname).toBe("/instances/hub-ada/threads/t1")
      })
    })
  })

  describe("a notification", () => {
    const raised: NotificationRaised = {
      type: "notification.raised",
      source: "inbox-sort",
      title: "Inbox sorted",
      text: "Three emails need an answer.",
      options: ["Archive them", "Leave them"],
    }

    /** The notification the hub keeps as n1, delivered on each channel as `statuses` say. */
    function kept(fields: Partial<Notification> = {}): Notification {
      return {
        notification_id: "n1",
        instance_id: "hub-ada",
        thread_id: "t1",
        header: "Ada · inbox-sort",
        title: "Inbox sorted",
        text: "Three emails need an answer.",
        options: ["Archive them", "Leave them"],
        link: null,
        raised_at: "2026-09-28T10:00:00Z",
        deliveries: [{ channel_id: "c1", status: "sent" }],
        replies: [],
        ...fields,
      }
    }

    /** Open a thread whose one turn raised the notification, then `answers` for it. */
    async function notified(answers: Payload[], hubAnswers: Answers = {}) {
      const events = thread(
        ["turn-1", started("Sort my inbox")],
        ["turn-1", raised],
        ...answers.map((payload): [string, Payload] => ["turn-1", payload]),
      )
      const hub = hubClient(hubAnswers)
      const clock = fakeClock()
      const { subscription } = openThread({}, undefined, hub, clock)
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      const card = screen.getByRole("article", { name: "Inbox sorted" })
      return { hub, clock, subscription, sequence: events.length, card }
    }

    /** The user's reply to the notification, as the hub records it. */
    const reply = (
      status: Notification["replies"][number]["status"],
      outcome: string | null = null,
    ): Notification["replies"][number] => ({
      channel_id: null,
      option: "Archive them",
      received_at: "2026-09-28T10:01:00Z",
      status,
      outcome,
    })

    const accepted: Payload = { type: "notification.accepted", raised: 2, notification_id: "n1" }

    it("shows its title, text, options and that it was delivered", async () => {
      const { hub, card } = await notified([accepted], {
        "notification.list": () => ({ notifications: [kept()] }),
      })

      expect(within(card).getByText("Three emails need an answer.")).toBeDefined()
      expect(within(card).getByRole("button", { name: "Archive them" })).toBeDefined()
      expect(within(card).getByRole("button", { name: "Leave them" })).toBeDefined()
      expect(await within(card).findByText("Delivered")).toBeDefined()
      expect(hub.calls).toEqual([{ method: "notification.list", params: { thread_id: "t1" } }])
    })

    it("shows as not delivered when every delivery failed", async () => {
      const deliveries = [
        { channel_id: "c1", status: "failed" as const },
        { channel_id: "c2", status: "failed" as const },
      ]
      const { card } = await notified([accepted], {
        "notification.list": () => ({ notifications: [kept({ deliveries })] }),
      })

      expect(await within(card).findByText("Not delivered")).toBeDefined()
    })

    it("shows why the hub refused it, and offers its options to no answer", async () => {
      const { hub, card } = await notified([
        { type: "notification.refused", raised: 2, reason: "The daily cap is reached." },
      ])

      expect(within(card).getByText("Not sent: The daily cap is reached.")).toBeDefined()
      const option = within(card).getByRole("button", { name: "Archive them" })
      expect((option as HTMLButtonElement).disabled).toBe(true)
      expect(hub.calls).toEqual([])
    })

    it("answers an option through the hub, and shows it answering until the hub applies it", async () => {
      let replies: Notification["replies"] = []
      const { hub, clock, card } = await notified([accepted], {
        "notification.list": () => ({ notifications: [kept({ replies })] }),
        "notification.reply": () => {
          replies = [reply("received")]
          return reply("received")
        },
      })
      await within(card).findByText("Delivered")

      await userEvent.click(within(card).getByRole("button", { name: "Archive them" }))

      expect(hub.calls).toContainEqual({
        method: "notification.reply",
        params: { notification_id: "n1", option: "Archive them" },
      })
      expect(await within(card).findByText("Answering “Archive them”")).toBeDefined()
      expect(within(card).queryByRole("button", { name: "Leave them" })).toBeNull()

      replies = [reply("applied", 'Answered "Archive them".')]
      await act(() => clock.advance(5_000))

      expect(within(card).getByText("Answered “Archive them”")).toBeDefined()
    })

    it("offers its options again, saying why, when the hub could not apply the answer", async () => {
      const outcome =
        "Not known whether the instance got this answer. Answer again from the web app."
      const { card } = await notified([accepted], {
        "notification.list": () => ({
          notifications: [kept({ replies: [reply("failed", outcome)] })],
        }),
      })

      expect(await within(card).findByText(outcome)).toBeDefined()
      const option = within(card).getByRole("button", { name: "Archive them" })
      expect((option as HTMLButtonElement).disabled).toBe(false)
    })

    it("shows an answer sent through a channel once the turn it starts reaches the thread", async () => {
      let replies: Notification["replies"] = []
      const { subscription, sequence, card } = await notified([accepted], {
        "notification.list": () => ({ notifications: [kept({ replies })] }),
      })
      await within(card).findByText("Delivered")

      replies = [reply("applied", 'Answered "Archive them".')]
      await act(async () =>
        subscription().deliver({
          sequence: sequence + 1,
          thread_id: "t1",
          turn_id: "turn-2",
          timestamp: "2026-09-28T10:01:00Z",
          payload: started("Archive them"),
        }),
      )

      expect(await within(card).findByText("Answered “Archive them”")).toBeDefined()
    })

    describe("raised while a turn is parked on an approval", () => {
      const request = {
        type: "approval.requested" as const,
        approval_id: "a1",
        name: "bash",
        arguments: { command: "make deploy" },
        rule: "mode.ask.write",
      }
      const parked: Payload[] = [
        started("Deploy it"),
        { type: "tool.call", call_id: "c1", name: "bash", arguments: { command: "make deploy" } },
        request,
      ]
      const asks: NotificationRaised = {
        type: "notification.raised",
        source: "Deploy it",
        title: "Approval waiting",
        text: "bash: command=make deploy",
        options: ["Approve", "Deny"],
        approval: { turn_id: "turn-1", request },
      }
      const approved: Payload = {
        type: "tool.gated",
        call_id: "c1",
        name: "bash",
        action: "allow",
        decided_by: "user",
        rule: "mode.ask.write",
      }

      /** Open thread t1 parked on approval a1, with `notification` raised and kept by the hub as n1. */
      async function opened(notification: NotificationRaised, hubAnswers: Answers = {}) {
        const { title, options } = notification
        const hub = hubClient({
          "notification.list": () => ({ notifications: [kept({ title, options })] }),
          ...hubAnswers,
        })
        const events = thread(
          ...[
            ...parked,
            notification,
            { type: "notification.accepted", raised: 4, notification_id: "n1" } as const,
          ].map((payload): [string, Payload] => ["turn-1", payload]),
        )
        const { subscription } = openThread(
          {
            "thread.approval.respond": () => ({ sequence: 5, thread_id: "t1", turn_id: "turn-1" }),
          },
          undefined,
          hub,
        )
        await act(async () => {
          subscription().subscribed(events.length)
          for (const event of events) subscription().deliver(event)
        })
        const card = screen.getByRole("article", { name: title })
        await within(card).findByText("Delivered")
        const deliver = (payload: Payload) =>
          act(async () =>
            subscription().deliver({
              sequence: events.length + 1,
              thread_id: "t1",
              turn_id: "turn-1",
              timestamp: "2026-09-28T10:00:00Z",
              payload,
            }),
          )
        return { hub, card, deliver }
      }

      it("shows the answer given through the approval panel in place of its options", async () => {
        const { card, deliver } = await opened(asks)
        const panel = screen.getByRole("region", { name: "Approve bash make deploy?" })

        await userEvent.click(within(panel).getByRole("button", { name: "Approve" }))
        await deliver(approved)

        expect(within(card).getByText("Approved")).toBeDefined()
        expect(within(card).queryByRole("button", { name: "Approve" })).toBeNull()
        expect(within(card).queryByRole("button", { name: "Deny" })).toBeNull()
        expect(screen.getByText("bash make deploy · approved by you · running")).toBeDefined()
      })

      it("answers an option through the hub while the turn waits", async () => {
        const { hub, card } = await opened(asks, {
          "notification.reply": () => reply("received"),
        })

        expect(within(card).getByRole("button", { name: "Approve" })).toBeDefined()
        await userEvent.click(within(card).getByRole("button", { name: "Deny" }))

        expect(hub.calls).toContainEqual({
          method: "notification.reply",
          params: { notification_id: "n1", option: "Deny" },
        })
      })

      it("keeps the options of a notification that asks about no approval", async () => {
        const { card, deliver } = await opened(raised)

        await deliver(approved)

        expect(within(card).getByRole("button", { name: "Archive them" })).toBeDefined()
        expect(within(card).getByRole("button", { name: "Leave them" })).toBeDefined()
      })
    })
  })

  describe("a view the turn wrote", () => {
    const changed = (view: string, title: string, change: ViewChange): Payload => ({
      type: "view.changed",
      view,
      title,
      change,
    })

    it("shows a card naming each view and whether it was created or changed", async () => {
      const events = thread(
        ["turn-1", started("Track my applications")],
        ["turn-1", changed("applications", "Job board", "created")],
        ["turn-1", changed("reading", "Reading list", "changed")],
      )
      const { subscription } = openThread()

      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })

      const board = screen.getByRole("article", { name: "Job board" })
      expect(within(board).getByText("Created")).toBeDefined()
      const reading = screen.getByRole("article", { name: "Reading list" })
      expect(within(reading).getByText("Changed")).toBeDefined()
    })

    it("strikes through a deleted view instead of linking to it", async () => {
      const events = thread(
        ["turn-1", started("Drop the old board")],
        ["turn-1", changed("applications", "Job board", "deleted")],
      )
      const { subscription } = openThread()

      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })

      expect(screen.getByText("Job board").tagName).toBe("DEL")
      expect(screen.queryByRole("link", { name: "Job board" })).toBeNull()
    })

    it("opens the view's page from its card, as a new history entry", async () => {
      window.history.replaceState(null, "", "/instances/hub-ada/threads/t1")
      const events = thread(
        ["turn-1", started("Track my applications")],
        ["turn-1", changed("applications", "Job board", "created")],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      const link = screen.getByRole("link", { name: "Job board" })
      const entries = window.history.length

      await userEvent.click(link)

      const view = "/instances/hub-ada/views/applications"
      expect(link.getAttribute("href")).toBe(view)
      expect(window.location.pathname).toBe(view)
      expect(window.history.length).toBe(entries + 1)
    })
  })

  it("says why a thread it cannot follow did not load", async () => {
    const { subscription } = openThread()

    await act(async () =>
      subscription().fail(
        new CallError({ code: "NOT_FOUND", message: "No thread t1.", retryable: false }),
      ),
    )

    expect(loading()).toBeNull()
    expect(screen.getByRole("alert").textContent).toContain("No thread t1.")
  })
})

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

  /**
   * Open the thread with its replay parked on takeover k1, and the live view's subscription on
   * the hub, which keeps the page.
   */
  async function openParked() {
    const hub = hubClient({
      "thread.takeover.input": () => ({}),
      "thread.takeover.finish": () => ({ sequence: 3, thread_id: "t1", turn_id: "turn-1" }),
    })
    const opened = openThread({}, undefined, hub)
    const events = opened.subscription()
    await act(async () => {
      events.subscribed(parked.length)
      for (const event of parked) events.deliver(event)
    })
    const view = hub.subscriptions.find(
      (subscription) => subscription.method === "thread.takeover.start",
    )
    if (view === undefined) throw new Error("The panel did not start the live view.")
    const sent = () => hub.calls.filter((call) => call.method.startsWith("thread.takeover"))
    return { ...opened, view, sent }
  }

  it("takes the composer's place with the reason, and starts the live view of the takeover", async () => {
    const { view } = await openParked()
    const panel = screen.getByRole("region", { name: "Take over the browser" })

    expect(screen.queryByRole("textbox", { name: "Message Ada" })).toBeNull()
    expect(panel.textContent).toContain(reason)
    expect(view.params).toEqual(turn)
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
    const { view, sent } = await openParked()
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
      expect(sent().map((call) => call.params)).toEqual([
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
    const { sent } = await openParked()

    await userEvent.setup().click(screen.getByRole("button", { name: "Finish" }))

    expect(sent()).toEqual([{ method: "thread.takeover.finish", params: takeover }])
  })

  it("saves the browser as a profile by the name given, when finishing", async () => {
    const { sent } = await openParked()
    const user = userEvent.setup()

    await user.type(screen.getByRole("textbox", { name: "Save as a profile" }), "LinkedIn")
    await user.click(screen.getByRole("button", { name: "Finish" }))

    expect(sent()).toEqual([
      { method: "thread.takeover.finish", params: { ...takeover, profile: "LinkedIn" } },
    ])
  })
})
