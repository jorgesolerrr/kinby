import type { View, ViewSummary } from "@kinby/contract"
import { CallError } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { InstanceScreen } from "@/components/instance-screen"
import { drawnPage } from "@/components/view-testing"

const BOARD = { id: "applications", title: "Job board", updated_at: "2026-10-25T09:30:00Z" }
const READING = { id: "reading", title: "Reading list", updated_at: "2026-10-24T08:00:00Z" }
const TRIP = { id: "trip", title: "Trip plan", updated_at: "2026-10-26T07:00:00Z" }

const STARTS: Answers = {
  "thread.create": () => ({ id: "thread-1", created_at: "2026-10-26T08:00:00Z" }),
  "thread.turn.start": () => ({ sequence: 1, thread_id: "thread-1", turn_id: "turn-1" }),
}

function snapshot({ id, title, updated_at }: ViewSummary): View {
  return {
    id,
    title,
    updated_at,
    page: "<p id=note></p><script>note.textContent = kinby.data.note</script>",
    data: { note: `The ${title} itself.` },
  }
}

/** Ada's screen at `path`, on an instance whose views are `views`, answering `answers` too. */
async function openScreen(path: string, views: ViewSummary[], answers: Answers = {}) {
  window.history.replaceState(null, "", path)
  const subscriber = stubSubscriber()
  const caller = stubCaller({ "view.list": () => ({ views: [...views] }), ...answers })
  const clock = fakeClock()
  const page = render(
    <InstanceScreen
      client={{
        call: caller.call,
        subscribe: subscriber.subscribe,
        state: () => "connected",
        onStateChange: () => () => {},
      }}
      clock={clock}
      instanceId="hub-ada"
      name="Ada"
    />,
  )
  await act(() => clock.advance(0))
  /** The open view's following, given its snapshot. */
  const deliver = (view: ViewSummary) => {
    const following = subscriber.subscriptions.findLast(({ cancelled }) => !cancelled)
    if (following === undefined) throw new Error("The screen follows no view.")
    act(() => following.deliver(snapshot(view)))
    return following
  }
  return { page, caller, clock, deliver, subscriber }
}

const cards = () =>
  within(screen.getByRole("region", { name: "Views" }))
    .queryAllByRole("link")
    .map((link) => link.querySelector('[data-slot="item-title"]')?.textContent)

/** Write `text` in the composer and send it once Send is ready. */
async function send(text: string) {
  const user = userEvent.setup()
  await user.type(screen.getByRole("textbox", { name: "Message" }), text)
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Send" }).getAttribute("aria-disabled")).toBe(
      "false",
    ),
  )
  await user.click(screen.getByRole("button", { name: "Send" }))
}

async function askToDelete() {
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: /^Menu for / }))
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }))
  return { user, dialog: await screen.findByRole("alertdialog") }
}

beforeEach(() => {
  window.localStorage.clear()
})

describe("an instance's screen", () => {
  it("shows a card per view, the most recently changed first, and opens none", async () => {
    const { subscriber } = await openScreen("/instances/hub-ada/views", [BOARD, READING, TRIP])

    expect(cards()).toEqual(["Trip plan", "Job board", "Reading list"])
    expect(window.location.pathname).toBe("/instances/hub-ada/views")
    expect(subscriber.subscriptions).toEqual([])
  })

  it("opens a view from its card, as a new history entry", async () => {
    await openScreen("/instances/hub-ada/views", [BOARD, READING])
    const entries = window.history.length

    await userEvent.click(screen.getByRole("link", { name: /Reading list/ }))

    expect(window.location.pathname).toBe("/instances/hub-ada/views/reading")
    expect(window.history.length).toBe(entries + 1)
  })

  it("says the instance has no views yet", async () => {
    await openScreen("/instances/hub-ada/views", [])

    expect(screen.getByText(/^No views yet/)).toBeDefined()
    expect(cards()).toEqual([])
  })

  it("lists a view the instance makes while it is open", async () => {
    const views = [BOARD]
    const { clock } = await openScreen("/instances/hub-ada/views", views)

    views.push(TRIP)
    await act(() => clock.advance(5_000))

    expect(cards()).toEqual(["Trip plan", "Job board"])
  })

  it("starts a thread from its composer, and opens it", async () => {
    const { caller } = await openScreen("/instances/hub-ada/views", [BOARD], STARTS)

    await send("Plan my week")

    await waitFor(() =>
      expect(window.location.pathname).toBe("/instances/hub-ada/threads/thread-1"),
    )
    expect(window.location.search).toBe("")
    expect(
      caller.calls.filter(({ method }) => method.startsWith("thread.")).map(({ params }) => params),
    ).toEqual([{}, { thread_id: "thread-1", message: "Plan my week", skills: [] }])
  })
})

describe("a view opened from the screen", () => {
  it("shows the view its address names, following it, under its title", async () => {
    const { deliver } = await openScreen("/instances/hub-ada/views/reading", [BOARD, READING])

    const following = deliver(READING)

    expect(screen.getByRole("heading", { name: "Reading list" })).toBeDefined()
    expect(following.params).toEqual({ id: "reading" })
    const frame = await screen.findByTitle("Reading list")
    expect(drawnPage(frame).documentElement.innerHTML).toContain("The Reading list itself.")
  })

  it("holds its title, when the view changed and the composer in the page column, and draws the view in the column's gutters at full width", async () => {
    const { deliver } = await openScreen("/instances/hub-ada/views/reading", [BOARD, READING])
    deliver(READING)

    const frame = await screen.findByTitle("Reading list")
    const column = '[data-slot="page-column"]'
    expect(screen.getByRole("heading", { name: "Reading list" }).closest(column)).not.toBeNull()
    expect(screen.getByText(/^Changed /).closest(column)).not.toBeNull()
    expect(screen.getByRole("textbox", { name: "Message" }).closest(column)).not.toBeNull()
    expect(frame.closest(column)).toBeNull()
    expect(frame.closest('[data-slot="page-gutters"]')).not.toBeNull()
  })

  it("goes back to the screen", async () => {
    await openScreen("/instances/hub-ada/views/reading", [BOARD, READING])

    await userEvent.click(screen.getByRole("link", { name: "Back to Ada" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/views")
    expect(cards()).toEqual(["Job board", "Reading list"])
  })

  it("starts a thread with the message, and opens it with the view in the pane beside it", async () => {
    const { caller } = await openScreen(
      "/instances/hub-ada/views/reading",
      [BOARD, READING],
      STARTS,
    )
    const entries = window.history.length

    await send("Fold the read ones")

    await waitFor(() =>
      expect(window.location.pathname).toBe("/instances/hub-ada/threads/thread-1"),
    )
    expect(window.location.search).toBe("?pane=view.reading")
    expect(window.history.length).toBe(entries + 1)
    expect(
      caller.calls.filter(({ method }) => method.startsWith("thread.")).map(({ params }) => params),
    ).toEqual([{}, { thread_id: "thread-1", message: "Fold the read ones", skills: [] }])
  })

  it("says why a send failed, and keeps the message and the view", async () => {
    await openScreen("/instances/hub-ada/views/reading", [READING], {
      "thread.create": () => {
        throw new CallError({ code: "INTERNAL", message: "The disk is full.", retryable: false })
      },
    })

    await send("Fold the read ones")

    expect(await screen.findByText("The disk is full.")).toBeDefined()
    expect(window.location.pathname).toBe("/instances/hub-ada/views/reading")
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty(
      "value",
      "Fold the read ones",
    )
  })
})

describe("deleting a view from its menu", () => {
  it("deletes it once confirmed, and goes back to the screen", async () => {
    const { caller } = await openScreen(
      "/instances/hub-ada/views/applications",
      [TRIP, BOARD, READING],
      {
        "view.delete": () => ({ views: [TRIP, READING] }),
      },
    )
    const entries = window.history.length

    const { user, dialog } = await askToDelete()
    expect(within(dialog).getByRole("heading", { name: "Delete Job board?" })).toBeDefined()
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(() => expect(window.location.pathname).toBe("/instances/hub-ada/views"))
    expect(window.history.length).toBe(entries)
    expect(caller.calls.filter(({ method }) => method === "view.delete")).toEqual([
      { method: "view.delete", params: { id: "applications" } },
    ])
  })

  it("deletes nothing when the confirm is cancelled", async () => {
    const { caller } = await openScreen("/instances/hub-ada/views/applications", [BOARD], {
      "view.delete": () => ({ views: [] }),
    })

    const { user, dialog } = await askToDelete()
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
    expect(caller.calls.filter(({ method }) => method === "view.delete")).toEqual([])
  })

  it("says why a delete failed in the confirm, and keeps the view", async () => {
    await openScreen("/instances/hub-ada/views/applications", [BOARD], {
      "view.delete": () => {
        throw new CallError({ code: "INTERNAL", message: "The disk is full.", retryable: false })
      },
    })

    const { user, dialog } = await askToDelete()
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    expect(await within(dialog).findByText("The disk is full.")).toBeDefined()
    expect(window.location.pathname).toBe("/instances/hub-ada/views/applications")
  })
})
