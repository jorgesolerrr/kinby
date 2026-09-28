import { CallError, type Event } from "@kinby/contract"
import { type Answers, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ThreadPanel } from "@/components/thread-panel"
import { threadList } from "@/lib/thread-list"

type Payload = Event["payload"]

/** Open thread t1 of Ada's instance, from a client that answers from `answers`. */
function openThread(
  answers: Answers = {},
  client = { ...stubCaller(answers), ...stubSubscriber() },
) {
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
      const { client } = await reopenParked()

      await userEvent
        .setup()
        .type(screen.getByRole("textbox", { name: "Reason" }), "Use staging instead{Enter}{Enter}")

      expect(client.calls).toEqual([
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
      const { client } = await reopenParked()

      fireEvent.keyDown(screen.getByRole("textbox", { name: "Reason" }), {
        key: "Enter",
        isComposing: true,
      })

      expect(client.calls).toEqual([])
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
