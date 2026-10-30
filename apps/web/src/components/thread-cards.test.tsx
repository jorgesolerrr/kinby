import type { Notification, NotificationRaised, ViewChange } from "@kinby/contract"
import { type Answers, fakeClock } from "@kinby/contract/testing"
import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { hubClient, openThread, type Payload, started, thread } from "@/components/thread-testing"

describe("a thread's panel", () => {
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
})
