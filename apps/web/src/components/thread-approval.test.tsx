import { CallError, type Event } from "@kinby/contract"
import type { Answers } from "@kinby/contract/testing"
import { act, fireEvent, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { instanceClient, openThread, started, thread } from "@/components/thread-testing"
import { threadList } from "@/lib/thread-list"

describe("a thread's panel", () => {
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
})
