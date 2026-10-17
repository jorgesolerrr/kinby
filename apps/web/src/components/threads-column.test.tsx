import { CallError } from "@kinby/contract"
import type { ThreadListResult, ThreadSummary } from "@kinby/contract"
import { type Answers, fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ThreadsColumn } from "@/components/threads-column"
import { threadList } from "@/lib/thread-list"

const NOW = new Date(2026, 8, 27, 12, 0)

/** A thread last active `daysAgo` local days before `NOW`, at 10:00. */
function at(daysAgo: number): string {
  return new Date(2026, 8, 27 - daysAgo, 10, 0).toISOString()
}

function thread(fields: Pick<ThreadSummary, "id"> & Partial<ThreadSummary>): ThreadSummary {
  return {
    title: null,
    created_at: at(0),
    last_activity_at: at(0),
    status: "idle",
    mode: "ask",
    mode_pinned: false,
    archived: false,
    origin: { kind: "user" },
    ...fields,
  }
}

/** What `thread.list` answers with `threads` on an instance whose ceiling is full access. */
function listing(threads: ThreadSummary[]): ThreadListResult {
  return { threads, ceiling: "full-access", cursor: null }
}

const ada = instanceSummary({ instance_id: "hub-ada", persona_name: "Ada", manifest_id: "coder" })

/** Render Ada's threads column from an instance client that is connected and answers from `answers`. */
function openColumn(answers: Answers, onPicked = () => {}) {
  const client = {
    ...stubCaller(answers),
    state: () => "connected" as const,
    onStateChange: () => () => {},
  }
  const clock = fakeClock()
  render(<ThreadsColumn client={client} clock={clock} instance={ada} onPicked={onPicked} />)
  return { client, clock, user: userEvent.setup() }
}

/** The browser shows or hides the tab, as switching to another one does. */
function showPage(visibility: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: visibility })
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
  })
}

const threads = () => within(screen.getByRole("navigation", { name: "Threads" }))

/** The titles of the threads the column lists, in its order. */
const listed = async () => {
  await threads().findAllByRole("link")
  return threads()
    .getAllByRole("link")
    .map((link) => within(link).getByTitle(/./).textContent)
}

describe("an instance's threads column", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"], now: NOW })
    window.history.replaceState(null, "", "/instances/hub-ada")
    Reflect.deleteProperty(document, "visibilityState")
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("heads the column with the instance and links to its pages", async () => {
    openColumn({ "thread.list": () => listing([]) })

    expect(screen.getByRole("heading", { name: "Ada" })).toBeDefined()
    expect(screen.getByText("coder")).toBeDefined()
    expect(screen.getByRole("link", { name: "Memory" }).getAttribute("href")).toBe(
      "/instances/hub-ada/memory",
    )
    expect(screen.getByRole("link", { name: "Stats" }).getAttribute("href")).toBe(
      "/instances/hub-ada/stats",
    )
    expect(screen.getByRole("link", { name: "Configure" }).getAttribute("href")).toBe(
      "/instances/hub-ada/config",
    )
  })

  it("opens a page from its icon and marks it", async () => {
    const onPicked = vi.fn()
    const { user } = openColumn({ "thread.list": () => listing([]) }, onPicked)

    await user.click(screen.getByRole("link", { name: "Memory" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/memory")
    expect(screen.getByRole("link", { name: "Memory" }).getAttribute("aria-current")).toBe("page")
    expect(onPicked).toHaveBeenCalled()
  })

  it("groups the threads by the day they were last active, keeping the instance's order", async () => {
    openColumn({
      "thread.list": () =>
        listing([
          thread({ id: "t1", title: "Deploy notes", last_activity_at: at(0) }),
          thread({ id: "t2", title: "Review", last_activity_at: at(1) }),
          thread({ id: "t3", title: "Groceries", last_activity_at: at(3) }),
          thread({ id: "t4", title: "Taxes", last_activity_at: at(30) }),
        ]),
    })

    expect(await listed()).toEqual(["Deploy notes", "Review", "Groceries", "Taxes"])
    const groups = screen.getAllByRole("region").map((region) => ({
      day: region.getAttribute("aria-label"),
      titles: within(region)
        .getAllByRole("link")
        .map((link) => within(link).getByTitle(/./).textContent),
    }))
    expect(groups).toEqual([
      { day: "Today", titles: ["Deploy notes"] },
      { day: "Yesterday", titles: ["Review"] },
      { day: "This week", titles: ["Groceries"] },
      { day: "Earlier", titles: ["Taxes"] },
    ])
  })

  it("names each thread by its title and describes it by its status", async () => {
    openColumn({
      "thread.list": () =>
        listing([
          thread({ id: "t1", title: "Deploy notes", status: "running" }),
          thread({ id: "t2", title: "Pull request review", status: "awaiting_approval" }),
          thread({ id: "t3", title: "Nightly digest", status: "failed" }),
          thread({ id: "t4" }),
        ]),
    })

    await listed()
    const description = (name: string) =>
      (threads().getByRole("link", { name }).getAttribute("aria-describedby") ?? "")
        .split(" ")
        .map((id) => document.getElementById(id)?.textContent)
    expect(description("Deploy notes")).toEqual(["Working", "10:00 AM"])
    expect(description("Pull request review")).toEqual(["Needs you", "10:00 AM"])
    expect(description("Nightly digest")).toEqual(["Failed", "10:00 AM"])
    expect(description("Untitled thread")).toEqual(["10:00 AM"])
  })

  it("lists the instance's sidebar set, which leaves archived threads out", async () => {
    const { client } = openColumn({
      "thread.list": ({ filter }) =>
        listing(
          filter === "sidebar"
            ? [thread({ id: "t1", title: "Deploy notes" })]
            : [
                thread({ id: "t1", title: "Deploy notes" }),
                thread({ id: "t2", title: "Groceries", archived: true }),
              ],
        ),
    })

    expect(await listed()).toEqual(["Deploy notes"])
    expect(client.calls).toEqual([{ method: "thread.list", params: { filter: "sidebar" } }])
  })

  it("lists every thread of a core from before archiving, which refuses the filter", async () => {
    const { client } = openColumn({
      "thread.list": (params) => {
        if ("filter" in params) {
          throw new CallError({
            code: "INVALID_ARGUMENT",
            message: "filter: Extra inputs are not permitted",
            retryable: false,
          })
        }
        return listing([thread({ id: "t1", title: "Deploy notes" })])
      },
    })

    expect(await listed()).toEqual(["Deploy notes"])
    expect(client.calls.at(-1)).toEqual({ method: "thread.list", params: {} })
  })

  it("searches the titles", async () => {
    const { user } = openColumn({
      "thread.list": () =>
        listing([
          thread({ id: "t1", title: "Deploy notes" }),
          thread({ id: "t2", title: "Release plan" }),
        ]),
    })
    await listed()

    await user.type(screen.getByRole("searchbox", { name: "Search threads" }), "DEPLOY")

    expect(await listed()).toEqual(["Deploy notes"])
    await user.type(screen.getByRole("searchbox", { name: "Search threads" }), "s and more")
    expect(threads().queryAllByRole("link")).toEqual([])
    expect(screen.getByText('No thread matches "DEPLOYs and more"')).toBeDefined()
  })

  it("shows only the threads that need you", async () => {
    const { user } = openColumn({
      "thread.list": () =>
        listing([
          thread({ id: "t1", title: "Deploy notes", status: "running" }),
          thread({ id: "t2", title: "Pull request review", status: "awaiting_approval" }),
          thread({ id: "t3", title: "Nightly digest", status: "failed" }),
        ]),
    })
    await listed()

    await user.click(screen.getByRole("button", { name: "Needs you" }))

    expect(await listed()).toEqual(["Pull request review", "Nightly digest"])
  })

  it("shows the routine runs from every thread, quiet ones included, but not archived ones", async () => {
    const routine = { kind: "routine", name: "digest", trigger: "scheduled" } as const
    const { client, user } = openColumn({
      "thread.list": ({ filter }) =>
        listing(
          filter === "sidebar"
            ? [thread({ id: "t1", title: "Deploy notes" })]
            : [
                thread({ id: "t1", title: "Deploy notes" }),
                thread({ id: "t2", title: "Morning digest", origin: routine }),
                thread({ id: "t3", title: "Old digest", origin: routine, archived: true }),
              ],
        ),
    })
    await listed()

    await user.click(screen.getByRole("button", { name: "Routines" }))

    expect(await listed()).toEqual(["Morning digest"])
    expect(client.calls).toContainEqual({ method: "thread.list", params: { filter: "all" } })
    expect(screen.getByText("Routine run")).toBeDefined()
  })

  it("says when nothing needs you", async () => {
    const { user } = openColumn({
      "thread.list": () => listing([thread({ id: "t1", title: "Deploy notes" })]),
    })
    await listed()

    await user.click(screen.getByRole("button", { name: "Needs you" }))

    expect(await screen.findByText("Nothing needs you")).toBeDefined()
  })

  it("lists the threads again every 5 seconds while the page is visible, and not while hidden", async () => {
    let status: ThreadSummary["status"] = "running"
    const { clock } = openColumn({
      "thread.list": () => listing([thread({ id: "t1", title: "Deploy notes", status })]),
    })
    expect(await threads().findByText("Working")).toBeDefined()

    status = "awaiting_approval"
    await act(() => clock.advance(4_000))
    expect(threads().getByText("Working")).toBeDefined()
    await act(() => clock.advance(1_000))
    expect(threads().getByText("Needs you")).toBeDefined()

    showPage("hidden")
    status = "idle"
    await act(() => clock.advance(60_000))
    expect(threads().getByText("Needs you")).toBeDefined()

    showPage("visible")
    await act(() => clock.advance(5_000))
    expect(threads().queryByText("Needs you")).toBeNull()
  })

  it("selects a thread and puts it in the URL", async () => {
    const onPicked = vi.fn()
    const { user } = openColumn(
      {
        "thread.list": () =>
          listing([
            thread({ id: "t1", title: "Deploy notes" }),
            thread({ id: "t2", title: "Review" }),
          ]),
      },
      onPicked,
    )

    await user.click(await threads().findByRole("link", { name: "Review" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/threads/t2")
    expect(threads().getByRole("link", { name: "Review" }).getAttribute("aria-current")).toBe(
      "page",
    )
    expect(
      threads().getByRole("link", { name: "Deploy notes" }).getAttribute("aria-current"),
    ).toBeNull()
    expect(onPicked).toHaveBeenCalled()
  })

  it("starts an untitled thread from New thread and selects it", async () => {
    let listedThreads = [thread({ id: "t1", title: "Deploy notes" })]
    const { client, user } = openColumn({
      "thread.list": () => listing(listedThreads),
      "thread.create": () => {
        listedThreads = [thread({ id: "t2" }), ...listedThreads]
        return { id: "t2", created_at: at(0) }
      },
    })
    await listed()

    await user.click(screen.getByRole("button", { name: "New thread" }))

    expect(client.calls).toContainEqual({ method: "thread.create", params: {} })
    const created = await threads().findByRole("link", { name: "Untitled thread" })
    expect(created.getAttribute("aria-current")).toBe("page")
    expect(window.location.pathname).toBe("/instances/hub-ada/threads/t2")
  })

  it("shows what the open thread listed without waiting for the next poll", async () => {
    let title = "Deploy notes"
    const { client } = openColumn({ "thread.list": () => listing([thread({ id: "t1", title })]) })
    await listed()

    title = "Release plan"
    await act(() => threadList(client, "all").listKept())

    expect(await listed()).toEqual(["Release plan"])
  })

  it("links to the thread list page, which holds the archived threads", async () => {
    const { user } = openColumn({ "thread.list": () => listing([]) })

    await user.click(screen.getByRole("link", { name: "All threads and archived" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/threads")
  })
})
