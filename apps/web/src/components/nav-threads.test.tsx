import { CallError } from "@kinby/contract"
import type { ThreadListResult, ThreadSummary } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { NavThreads } from "@/components/nav-threads"
import { SidebarMenu, SidebarMenuItem, SidebarProvider } from "@/components/ui/sidebar"
import { threadList } from "@/lib/thread-list"

function thread(fields: Pick<ThreadSummary, "id"> & Partial<ThreadSummary>): ThreadSummary {
  return {
    title: null,
    created_at: "2026-09-27T10:00:00Z",
    last_activity_at: "2026-09-27T10:00:00Z",
    status: "idle",
    mode: "ask",
    mode_pinned: false,
    archived: false,
    ...fields,
  }
}

/** What `thread.list` answers with `threads` on an instance whose ceiling is full access. */
function listing(threads: ThreadSummary[]): ThreadListResult {
  return { threads, ceiling: "full-access" }
}

/** Render Ada's threads from an instance client that is connected and answers from `answers`. */
function openThreads(answers: Answers) {
  const client = {
    ...stubCaller(answers),
    state: () => "connected" as const,
    onStateChange: () => () => {},
  }
  const clock = fakeClock()
  render(
    <SidebarProvider>
      <SidebarMenu>
        <SidebarMenuItem>
          <NavThreads client={client} clock={clock} instanceId="hub-ada" />
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarProvider>,
  )
  return { client, clock }
}

/** The browser shows or hides the tab, as switching to another one does. */
function showPage(visibility: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: visibility })
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
  })
}

const threadLinks = async () => {
  await screen.findAllByRole("link")
  return screen.getAllByRole("link")
}

describe("an instance's threads", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("lists the threads in the order the instance returns them, badging the ones that need a look", async () => {
    openThreads({
      "thread.list": () =>
        listing([
          thread({ id: "t1", title: "Deploy notes", status: "running" }),
          thread({ id: "t2", title: "Pull request review", status: "awaiting_approval" }),
          thread({ id: "t3", title: "Nightly digest", status: "failed" }),
          thread({ id: "t4", title: "Groceries" }),
        ]),
    })

    const links = await threadLinks()

    expect(links.map((link) => link.textContent)).toEqual([
      "Deploy notesworking",
      "Pull request reviewneeds you",
      "Nightly digestfailed",
      "Groceries",
    ])
  })

  it("lists the instance's sidebar set, which leaves archived threads out", async () => {
    const { client } = openThreads({
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

    const links = await threadLinks()

    expect(links.map((link) => link.textContent)).toEqual(["Deploy notes"])
    expect(client.calls).toEqual([{ method: "thread.list", params: { filter: "sidebar" } }])
  })

  it("lists every thread of a core from before archiving, which refuses the filter", async () => {
    const { client } = openThreads({
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

    const links = await threadLinks()

    expect(links.map((link) => link.textContent)).toEqual(["Deploy notes"])
    expect(client.calls.at(-1)).toEqual({ method: "thread.list", params: {} })
  })

  it("keeps a long title on one line beside its badge", async () => {
    openThreads({
      "thread.list": () =>
        listing([thread({ id: "t1", title: "Pirate tools pass", status: "running" })]),
    })

    const row = within((await threadLinks())[0] as HTMLElement)

    const title = row.getByText("Pirate tools pass").className
    expect(title).toMatch(/\btruncate\b/)
    expect(title).toMatch(/\bmin-w-0\b/)
    expect(row.getByText("working").className).toMatch(/\bshrink-0\b/)
  })

  it("names a thread without a title as untitled", async () => {
    openThreads({ "thread.list": () => listing([thread({ id: "t1" })]) })

    expect((await threadLinks()).map((link) => link.textContent)).toEqual(["Untitled thread"])
  })

  it("lists the threads again every 5 seconds while the page is visible, and not while hidden", async () => {
    let threads = [thread({ id: "t1", title: "Deploy notes", status: "running" })]
    const { clock } = openThreads({ "thread.list": () => listing(threads) })
    const row = async () => within((await threadLinks())[0] as HTMLElement)
    expect((await row()).getByText("working")).toBeDefined()

    threads = [thread({ id: "t1", title: "Deploy notes", status: "awaiting_approval" })]
    await act(() => clock.advance(4_000))
    expect((await row()).getByText("working")).toBeDefined()
    await act(() => clock.advance(1_000))
    expect((await row()).getByText("needs you")).toBeDefined()

    showPage("hidden")
    threads = [thread({ id: "t1", title: "Deploy notes" })]
    await act(() => clock.advance(60_000))
    expect((await row()).getByText("needs you")).toBeDefined()

    showPage("visible")
    await act(() => clock.advance(5_000))
    expect((await row()).queryByText("needs you")).toBeNull()
  })

  it("selects a thread and puts it in the URL", async () => {
    openThreads({
      "thread.list": () =>
        listing([
          thread({ id: "t1", title: "Deploy notes" }),
          thread({ id: "t2", title: "Review" }),
        ]),
    })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("link", { name: "Review" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/threads/t2")
    expect(screen.getByRole("link", { name: "Review" }).getAttribute("aria-current")).toBe("page")
    expect(
      screen.getByRole("link", { name: "Deploy notes" }).getAttribute("aria-current"),
    ).toBeNull()
  })

  it("starts an untitled thread from the plus and selects it", async () => {
    let threads = [thread({ id: "t1", title: "Deploy notes" })]
    const { client } = openThreads({
      "thread.list": () => listing(threads),
      "thread.create": () => {
        threads = [thread({ id: "t2" }), ...threads]
        return { id: "t2", created_at: "2026-09-27T11:00:00Z" }
      },
    })
    const user = userEvent.setup()
    await threadLinks()

    await user.click(screen.getByRole("button", { name: "New thread" }))

    expect(client.calls).toContainEqual({ method: "thread.create", params: {} })
    const created = await screen.findByRole("link", { name: "Untitled thread" })
    expect(created.getAttribute("aria-current")).toBe("page")
    expect(window.location.pathname).toBe("/instances/hub-ada/threads/t2")
  })

  it("shows what the open thread listed without waiting for the next poll", async () => {
    let title = "Deploy notes"
    const { client } = openThreads({ "thread.list": () => listing([thread({ id: "t1", title })]) })
    await threadLinks()

    title = "Release plan"
    await act(() => threadList(client, "all").list())

    expect((await threadLinks()).map((link) => link.textContent)).toEqual(["Release plan"])
  })
})
