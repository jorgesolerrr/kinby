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

const tabs = () => within(screen.getByRole("tablist", { name: "Ada's views" }))
const openTab = () => tabs().getByRole("tab", { selected: true }).textContent

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
  it("shows one tab per view, the most recently changed first", async () => {
    await openScreen("/instances/hub-ada/views/applications", [BOARD, READING, TRIP])

    expect(
      tabs()
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Trip plan", "Job board", "Reading list"])
  })

  it("shows the tab its address names, following that view", async () => {
    const { deliver } = await openScreen("/instances/hub-ada/views/reading", [BOARD, READING])

    const following = deliver(READING)

    expect(openTab()).toBe("Reading list")
    expect(following.params).toEqual({ id: "reading" })
    const frame = await screen.findByTitle("Reading list")
    expect(drawnPage(frame).documentElement.innerHTML).toContain("The Reading list itself.")
  })

  it("opens a tab picked at its address, as a new history entry", async () => {
    await openScreen("/instances/hub-ada/views/applications", [BOARD, READING])
    const entries = window.history.length

    await userEvent.click(tabs().getByRole("tab", { name: "Reading list" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/views/reading")
    expect(window.history.length).toBe(entries + 1)
    expect(openTab()).toBe("Reading list")
  })

  it("opens on the most recently changed view when it names none", async () => {
    await openScreen("/instances/hub-ada/views", [BOARD, READING, TRIP])

    await waitFor(() => expect(window.location.pathname).toBe("/instances/hub-ada/views/trip"))
    expect(openTab()).toBe("Trip plan")
  })

  it("opens on the tab last open for the instance in this browser", async () => {
    const { page } = await openScreen("/instances/hub-ada/views/reading", [BOARD, READING])
    page.unmount()

    await openScreen("/instances/hub-ada/views", [BOARD, READING])

    await waitFor(() => expect(window.location.pathname).toBe("/instances/hub-ada/views/reading"))
    expect(openTab()).toBe("Reading list")
  })

  it("opens on the most recently changed view once the last one open is gone", async () => {
    const { page } = await openScreen("/instances/hub-ada/views/reading", [BOARD, READING])
    page.unmount()

    await openScreen("/instances/hub-ada/views", [BOARD])

    await waitFor(() =>
      expect(window.location.pathname).toBe("/instances/hub-ada/views/applications"),
    )
  })

  it("lands on the composer sent to the instance when it has no views", async () => {
    const entries = window.history.length
    await openScreen("/instances/hub-ada/views", [])

    await waitFor(() => expect(window.location.pathname).toBe("/"))
    expect(window.location.search).toBe("?to=hub-ada")
    expect(window.history.length).toBe(entries)
  })

  it("lists a view the instance makes while it is open", async () => {
    const views = [BOARD]
    const { clock } = await openScreen("/instances/hub-ada/views/applications", views)

    views.push(TRIP)
    await act(() => clock.advance(5_000))

    expect(
      tabs()
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Trip plan", "Job board"])
  })
})

describe("deleting a view from its tab's menu", () => {
  it("deletes it once confirmed, and shows the next view", async () => {
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

    await waitFor(() => expect(window.location.pathname).toBe("/instances/hub-ada/views/reading"))
    expect(window.history.length).toBe(entries)
    expect(caller.calls.filter(({ method }) => method === "view.delete")).toEqual([
      { method: "view.delete", params: { id: "applications" } },
    ])
  })

  it("shows the one before it when it was the last tab", async () => {
    await openScreen("/instances/hub-ada/views/reading", [BOARD, READING], {
      "view.delete": () => ({ views: [BOARD] }),
    })

    const { user, dialog } = await askToDelete()
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(() =>
      expect(window.location.pathname).toBe("/instances/hub-ada/views/applications"),
    )
  })

  it("lands on the composer once the last view is deleted", async () => {
    await openScreen("/instances/hub-ada/views/applications", [BOARD], {
      "view.delete": () => ({ views: [] }),
    })

    const { user, dialog } = await askToDelete()
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(() => expect(window.location.pathname).toBe("/"))
    expect(window.location.search).toBe("?to=hub-ada")
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
