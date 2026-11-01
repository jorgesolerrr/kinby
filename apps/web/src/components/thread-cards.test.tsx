import type { Notification, NotificationRaised, View, ViewChange } from "@kinby/contract"
import { type Answers, fakeClock } from "@kinby/contract/testing"
import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it } from "vitest"

import {
  hubClient,
  openThread,
  type Payload,
  started,
  thread,
  traceLine,
} from "@/components/thread-testing"

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

    /** Open a thread whose one turn raised `notification`, then `answers` for it. */
    async function notified(
      answers: Payload[],
      hubAnswers: Answers = {},
      notification: NotificationRaised = raised,
    ) {
      const events = thread(
        ["turn-1", started("Sort my inbox")],
        ["turn-1", notification],
        ...answers.map((payload): [string, Payload] => ["turn-1", payload]),
      )
      const hub = hubClient(hubAnswers)
      const clock = fakeClock()
      const { subscription } = openThread({}, undefined, hub, clock)
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      return { hub, clock, subscription, sequence: events.length }
    }

    const card = () => screen.getByRole("article", { name: "Inbox sorted" })

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
      const { hub } = await notified([accepted], {
        "notification.list": () => ({ notifications: [kept()] }),
      })

      expect(within(card()).getByText("Three emails need an answer.")).toBeDefined()
      expect(within(card()).getByRole("button", { name: "Archive them" })).toBeDefined()
      expect(within(card()).getByRole("button", { name: "Leave them" })).toBeDefined()
      expect(await within(card()).findByText("Delivered")).toBeDefined()
      expect(hub.calls).toEqual([{ method: "notification.list", params: { thread_id: "t1" } }])
    })

    it("shows as not delivered when every delivery failed", async () => {
      const deliveries = [
        { channel_id: "c1", status: "failed" as const },
        { channel_id: "c2", status: "failed" as const },
      ]
      await notified([accepted], {
        "notification.list": () => ({ notifications: [kept({ deliveries })] }),
      })

      expect(await within(card()).findByText("Not delivered")).toBeDefined()
    })

    it("shows as a line with its title and delivery status when it has no options", async () => {
      const told: NotificationRaised = { ...raised, options: [] }
      await notified(
        [accepted],
        { "notification.list": () => ({ notifications: [kept(told)] }) },
        told,
      )

      expect(await screen.findByText("Inbox sorted · Delivered")).toBeDefined()
      expect(screen.queryByRole("article", { name: "Inbox sorted" })).toBeNull()
    })

    it("shows as a line saying why the hub refused it, with no options", async () => {
      const { hub } = await notified([
        { type: "notification.refused", raised: 2, reason: "The daily cap is reached." },
      ])

      expect(screen.getByText("Inbox sorted · Not sent: The daily cap is reached.")).toBeDefined()
      expect(screen.queryByRole("button", { name: "Archive them" })).toBeNull()
      expect(hub.calls).toEqual([])
    })

    it("answers an option through the hub, and shows it answering until the hub applies it", async () => {
      let replies: Notification["replies"] = []
      const { hub, clock } = await notified([accepted], {
        "notification.list": () => ({ notifications: [kept({ replies })] }),
        "notification.reply": () => {
          replies = [reply("received")]
          return reply("received")
        },
      })
      await within(card()).findByText("Delivered")

      await userEvent.click(within(card()).getByRole("button", { name: "Archive them" }))

      expect(hub.calls).toContainEqual({
        method: "notification.reply",
        params: { notification_id: "n1", option: "Archive them" },
      })
      expect(await within(card()).findByText("Answering “Archive them”")).toBeDefined()
      expect(within(card()).queryByRole("button", { name: "Leave them" })).toBeNull()

      replies = [reply("applied", 'Answered "Archive them".')]
      await act(() => clock.advance(5_000))

      expect(screen.getByText("Inbox sorted · Delivered · Answered “Archive them”")).toBeDefined()
      expect(screen.queryByRole("article", { name: "Inbox sorted" })).toBeNull()
    })

    it("offers its options again, saying why, when the hub could not apply the answer", async () => {
      const outcome =
        "Not known whether the instance got this answer. Answer again from the web app."
      await notified([accepted], {
        "notification.list": () => ({
          notifications: [kept({ replies: [reply("failed", outcome)] })],
        }),
      })

      expect(await within(card()).findByText(outcome)).toBeDefined()
      const option = within(card()).getByRole("button", { name: "Archive them" })
      expect((option as HTMLButtonElement).disabled).toBe(false)
    })

    it("shows an answer sent through a channel once the turn it starts reaches the thread", async () => {
      let replies: Notification["replies"] = []
      const { subscription, sequence } = await notified([accepted], {
        "notification.list": () => ({ notifications: [kept({ replies })] }),
      })
      await within(card()).findByText("Delivered")

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

      expect(
        await screen.findByText("Inbox sorted · Delivered · Answered “Archive them”"),
      ).toBeDefined()
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

      it("shows as a line with the answer given through the approval panel", async () => {
        const { deliver } = await opened(asks)
        const panel = screen.getByRole("region", { name: "Approve bash make deploy?" })

        await userEvent.click(within(panel).getByRole("button", { name: "Approve" }))
        await deliver(approved)

        expect(screen.getByText("Approval waiting · Delivered · Approved")).toBeDefined()
        expect(screen.queryByRole("article", { name: "Approval waiting" })).toBeNull()
        await userEvent.click(traceLine("Running bash make deploy"))
        expect(
          within(traceLine("bash make deploy")).getByText("approved by you · running"),
        ).toBeDefined()
      })

      it("shows as a line once the turn ends before the approval is answered", async () => {
        const { deliver } = await opened(asks)

        await deliver({ type: "turn.interrupted" })

        expect(screen.getByText("Approval waiting · Delivered · No longer waiting")).toBeDefined()
        expect(screen.queryByRole("button", { name: "Deny" })).toBeNull()
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

    it("shows a line linking to each view, saying whether it was created or changed", async () => {
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

      expect(screen.getByRole("link", { name: "Created the view Job board" })).toBeDefined()
      expect(screen.getByRole("link", { name: "Changed the view Reading list" })).toBeDefined()
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
      expect(screen.queryByRole("link")).toBeNull()
    })

    describe("its pane", () => {
      afterEach(() => window.history.replaceState(null, "", "/"))

      const threadPath = "/instances/hub-ada/threads/t1"
      const viewPath = "/instances/hub-ada/views/applications"
      const snapshot = (note: string, updated_at = "2026-10-25T09:30:00Z"): View => ({
        id: "applications",
        title: "Job board",
        updated_at,
        spec: { components: [{ id: "root", component: "Text", text: { path: "/note" } }] },
        data: { note },
      })

      /** Open the thread whose turn created the view, at `url`. */
      async function openWritten(url = threadPath) {
        window.history.replaceState(null, "", url)
        const events = thread(
          ["turn-1", started("Track my applications")],
          ["turn-1", changed("applications", "Job board", "created")],
        )
        const panel = openThread()
        // The thread's subscription comes first, and the view's once the pane opens.
        const [turns] = panel.client.subscriptions
        await act(async () => {
          turns.subscribed(events.length)
          for (const event of events) turns.deliver(event)
        })
        /** The pane's following of the view, once it opens. */
        const following = () => {
          const view = panel.client.subscriptions.find(({ method }) => method === "view.subscribe")
          if (view === undefined) throw new Error("The pane is not following the view.")
          return view
        }
        return { following }
      }

      const marker = () => screen.getByRole("link", { name: "Created the view Job board" })
      const pane = () => screen.getByRole("region", { name: "Job board" })

      it("opens from the view's line, which still links to the view's page", async () => {
        const { following } = await openWritten()
        const entries = window.history.length

        await userEvent.click(marker())
        await act(async () => following().deliver(snapshot("Acme: interview on Monday.")))

        expect(marker().getAttribute("href")).toBe(viewPath)
        expect(window.location.pathname).toBe(threadPath)
        expect(new URLSearchParams(window.location.search).get("pane")).toBe("view.applications")
        expect(window.history.length).toBe(entries + 1)
        expect(following().params).toEqual({ id: "applications" })
        expect(pane().textContent).toContain("Changed Oct 25, 2026")
        expect(pane().textContent).toContain("Acme: interview on Monday.")
      })

      it("follows the view: a new snapshot shows, and an ended subscription shows it deleted", async () => {
        const { following } = await openWritten()
        await userEvent.click(marker())
        await act(async () => following().deliver(snapshot("Acme: interview on Monday.")))

        await act(async () => following().deliver(snapshot("Acme: offer.", "2026-10-26T08:00:00Z")))
        expect(pane().textContent).toContain("Acme: offer.")
        expect(pane().textContent).toContain("Changed Oct 26, 2026")

        await act(async () => following().end())
        expect(await screen.findByText("This view was deleted")).toBeDefined()
        expect(screen.queryByText("Acme: offer.")).toBeNull()
      })

      it("opens the view's page from its header", async () => {
        const { following } = await openWritten()
        await userEvent.click(marker())
        await act(async () => following().deliver(snapshot("Acme: interview on Monday.")))

        await userEvent.click(within(pane()).getByRole("button", { name: "Open as a page" }))

        expect(window.location.pathname).toBe(viewPath)
        expect(window.location.search).toBe("")
      })

      it("closes and stops following the view", async () => {
        const { following } = await openWritten()
        await userEvent.click(marker())
        await act(async () => following().deliver(snapshot("Acme: interview on Monday.")))

        await userEvent.click(within(pane()).getByRole("button", { name: "Close" }))

        expect(screen.queryByRole("region", { name: "Job board" })).toBeNull()
        expect(following().cancelled).toBe(true)
        expect(window.location.search).toBe("")
      })

      it("shows when the thread opens with it in the URL", async () => {
        const { following } = await openWritten(`${threadPath}?pane=view.applications`)

        await act(async () => following().deliver(snapshot("Acme: interview on Monday.")))

        expect(pane().textContent).toContain("Acme: interview on Monday.")
      })
    })
  })
})
