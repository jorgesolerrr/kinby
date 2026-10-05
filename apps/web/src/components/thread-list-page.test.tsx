import type { RoutineSummary, ThreadListResult, ThreadSummary } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { NavThreads } from "@/components/nav-threads"
import { ThreadListPage } from "@/components/thread-list-page"
import { SidebarMenu, SidebarMenuItem, SidebarProvider } from "@/components/ui/sidebar"

function thread(fields: Pick<ThreadSummary, "id"> & Partial<ThreadSummary>): ThreadSummary {
  return {
    title: null,
    created_at: "2026-09-27T10:00:00Z",
    last_activity_at: "2026-09-27T10:00:00Z",
    status: "idle",
    mode: "ask",
    mode_pinned: false,
    archived: false,
    origin: { kind: "user" },
    ...fields,
  }
}

function page(threads: ThreadSummary[], cursor: ThreadListResult["cursor"] = null) {
  return { threads, ceiling: "full-access" as const, cursor }
}

function routine(name: string): RoutineSummary {
  return {
    name,
    description: "",
    enabled: true,
    last_run: null,
    mode: "ask",
    next_run: null,
    pending: 0,
    schedule: null,
  }
}

const NEWS = { kind: "routine", name: "news", trigger: "scheduled" } as const

/** Open Ada's thread list on a client that answers from `answers`, with no routines unless given. */
function openPage(answers: Answers) {
  const client = stubCaller({ "routine.list": () => ({ routines: [], warnings: [] }), ...answers })
  render(<ThreadListPage client={client} clock={fakeClock()} instanceId="hub-ada" />)
  return { client }
}

/** Each row's cells, as text, below the header row. */
async function rows() {
  const table = await screen.findByRole("table", { name: "Threads" })
  await within(table).findAllByRole("cell")
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    )
}

describe("the thread list page", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/instances/hub-ada/threads")
  })

  it("shows each thread's title, origin, status and last activity, in the order listed", async () => {
    openPage({
      "thread.list": () =>
        page([
          thread({
            id: "t1",
            title: "Morning news",
            status: "failed",
            origin: NEWS,
            last_activity_at: "2026-09-28T09:00:00Z",
          }),
          thread({ id: "t2", title: "Deploy notes", last_activity_at: "2026-09-27T15:30:00Z" }),
        ]),
    })

    const listed = await rows()

    expect(listed.map((cells) => cells.slice(0, 3))).toEqual([
      ["Morning news", "news", "failed"],
      ["Deploy notes", "You", "idle"],
    ])
    expect(listed[0]?.[3]).toMatch(/Sep 28, 2026/)
    expect(listed[1]?.[3]).toMatch(/Sep 27, 2026/)
  })

  it("opens a thread from anywhere on its row", async () => {
    openPage({
      "thread.list": () =>
        page([
          thread({ id: "t1", title: "Morning news", origin: NEWS }),
          thread({ id: "t2", title: "Deploy notes" }),
        ]),
    })
    const user = userEvent.setup()
    await rows()

    await user.click(screen.getByRole("cell", { name: "You" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/threads/t2")
    expect(screen.getByRole("link", { name: "Morning news" }).getAttribute("href")).toBe(
      "/instances/hub-ada/threads/t1",
    )
    await user.click(screen.getByRole("link", { name: "Morning news" }))
    expect(window.location.pathname).toBe("/instances/hub-ada/threads/t1")
  })

  it("filters to the archived threads or to one routine's runs through thread.list", async () => {
    const { client } = openPage({
      "routine.list": () => ({
        routines: [routine("news"), routine("standup")],
        warnings: [],
      }),
      "thread.list": ({ filter, routine }) =>
        page(
          filter === "archived"
            ? [thread({ id: "t3", title: "Groceries", archived: true })]
            : routine === "news"
              ? [thread({ id: "t1", title: "Morning news", origin: NEWS })]
              : [
                  thread({ id: "t1", title: "Morning news", origin: NEWS }),
                  thread({ id: "t2", title: "Deploy notes" }),
                ],
        ),
    })
    const user = userEvent.setup()
    const titles = async () => (await rows()).map((cells) => cells[0])
    expect(await titles()).toEqual(["Morning news", "Deploy notes"])

    await user.click(screen.getByRole("combobox", { name: "Show" }))
    await user.click(await screen.findByRole("option", { name: "Archived" }))
    expect(await titles()).toEqual(["Groceries"])

    await user.click(screen.getByRole("combobox", { name: "Show" }))
    await user.click(await screen.findByRole("option", { name: "news" }))
    expect(await titles()).toEqual(["Morning news"])

    expect(client.calls.filter((call) => call.method === "thread.list")).toEqual([
      { method: "thread.list", params: { filter: "all", limit: 50 } },
      { method: "thread.list", params: { filter: "archived", limit: 50 } },
      { method: "thread.list", params: { filter: "all", routine: "news", limit: 50 } },
    ])
  })

  it("loads the next page through the cursor until thread.list returns none", async () => {
    const cursor = { id: "t2", last_activity_at: "2026-09-27T10:00:00Z" }
    const { client } = openPage({
      "thread.list": (command) =>
        command.cursor == null
          ? page(
              [thread({ id: "t1", title: "Morning news" }), thread({ id: "t2", title: "Standup" })],
              cursor,
            )
          : page([thread({ id: "t3", title: "Deploy notes" })]),
    })
    const user = userEvent.setup()
    await rows()

    await user.click(screen.getByRole("button", { name: "Load more" }))

    expect((await rows()).map((cells) => cells[0])).toEqual([
      "Morning news",
      "Standup",
      "Deploy notes",
    ])
    expect(client.calls.at(-1)).toEqual({
      method: "thread.list",
      params: { filter: "all", limit: 50, cursor },
    })
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull()
  })

  it("searches the loaded threads by title without asking the instance", async () => {
    const { client } = openPage({
      "thread.list": () =>
        page([
          thread({ id: "t1", title: "Morning news" }),
          thread({ id: "t2", title: "Deploy notes" }),
          thread({ id: "t3", title: "News digest" }),
          thread({ id: "t4" }),
        ]),
    })
    const user = userEvent.setup()
    await rows()

    await user.type(screen.getByRole("searchbox", { name: "Search titles" }), "news")

    expect((await rows()).map((cells) => cells[0])).toEqual(["Morning news", "News digest"])
    expect(client.calls.filter((call) => call.method === "thread.list")).toHaveLength(1)
  })

  it("archives and unarchives a thread from its row, and the sidebar follows", async () => {
    let threads = [
      thread({ id: "t1", title: "Deploy notes" }),
      thread({ id: "t2", title: "Groceries", archived: true }),
    ]
    const put = (thread_id: string, archived: boolean) => {
      threads = threads.map((listed) =>
        listed.id === thread_id ? { ...listed, archived } : listed,
      )
      return threads.find((listed) => listed.id === thread_id) as ThreadSummary
    }
    const client = {
      ...stubCaller({
        "routine.list": () => ({ routines: [], warnings: [] }),
        "thread.list": ({ filter }) =>
          page(filter === "sidebar" ? threads.filter((listed) => !listed.archived) : threads),
        "thread.archive": ({ thread_id }) => put(thread_id, true),
        "thread.unarchive": ({ thread_id }) => put(thread_id, false),
      }),
      state: () => "connected" as const,
      onStateChange: () => () => {},
    }
    const clock = fakeClock()
    render(
      <SidebarProvider>
        <nav aria-label="Sidebar">
          <SidebarMenu>
            <SidebarMenuItem>
              <NavThreads client={client} clock={clock} instanceId="hub-ada" />
            </SidebarMenuItem>
          </SidebarMenu>
        </nav>
        <ThreadListPage client={client} clock={clock} instanceId="hub-ada" />
      </SidebarProvider>,
    )
    const user = userEvent.setup()
    const sidebar = within(screen.getByRole("navigation", { name: "Sidebar" }))
    const sidebarTitles = () => sidebar.queryAllByRole("link").map((link) => link.textContent)
    const row = async (title: string) =>
      within(await screen.findByRole("row", { name: new RegExp(title) }))
    expect(await sidebar.findByRole("link", { name: "Deploy notes" })).toBeDefined()

    await user.click((await row("Deploy notes")).getByRole("button", { name: "Archive" }))

    expect(await (await row("Deploy notes")).findByRole("button", { name: "Unarchive" }))
    expect(sidebarTitles()).toEqual([])
    expect(window.location.pathname).toBe("/instances/hub-ada/threads")

    await user.click((await row("Groceries")).getByRole("button", { name: "Unarchive" }))

    expect(await (await row("Groceries")).findByRole("button", { name: "Archive" }))
    expect(sidebarTitles()).toEqual(["Groceries"])
  })
})
