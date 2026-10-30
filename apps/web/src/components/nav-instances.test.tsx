import type {
  InstanceClient,
  InstanceSummary,
  ThreadFilter,
  ThreadSummary,
  ViewSummary,
} from "@kinby/contract"
import {
  type FakeClock,
  fakeClock,
  instanceSummary,
  stubCaller,
  stubSubscriber,
} from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type * as React from "react"
import { useState } from "react"
import { beforeEach, describe, expect, it } from "vitest"

import { type Folds, NavInstances } from "@/components/nav-instances"
import { SidebarProvider } from "@/components/ui/sidebar"
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
    origin: { kind: "user" },
    ...fields,
  }
}

/** The threads in `filter`'s set, picked the way the instance picks them. */
function inSet(threads: ThreadSummary[], filter: ThreadFilter | undefined): ThreadSummary[] {
  switch (filter ?? "sidebar") {
    case "sidebar":
      return threads.filter((t) => !t.archived || t.status === "awaiting_approval")
    case "archived":
      return threads.filter((t) => t.archived)
    case "all":
      return threads
  }
}

/**
 * A hub whose instances list the threads `threads` and the views `views` hold for them, on
 * connections that stay up.
 */
function fakeHub(
  threads: Record<string, ThreadSummary[]>,
  views: Record<string, ViewSummary[]> = {},
) {
  const opened: string[] = []
  const closed: string[] = []
  return {
    threads,
    views,
    opened,
    closed,
    instance(instanceId: string): InstanceClient {
      opened.push(instanceId)
      return {
        ...stubCaller({
          "thread.list": ({ filter }) => ({
            threads: inSet(threads[instanceId] ?? [], filter),
            ceiling: "full-access",
            cursor: null,
          }),
          "view.list": () => ({ views: views[instanceId] ?? [] }),
        }),
        ...stubSubscriber(),
        state: () => "connected",
        onStateChange: () => () => {},
        close: () => closed.push(instanceId),
      }
    },
  }
}

type Hub = ReturnType<typeof fakeHub>

function sidebar(
  hub: Hub,
  clock: FakeClock,
  instances: InstanceSummary[],
  selected?: InstanceSummary,
  selectedClient?: Pick<InstanceClient, "call" | "state" | "onStateChange">,
): React.ReactElement {
  return (
    <SidebarProvider>
      <Folding
        client={hub}
        clock={clock}
        instances={instances}
        selected={selected}
        creating={false}
        selectedClient={selectedClient}
      />
    </SidebarProvider>
  )
}

/** The groups, keeping the user's folds as the sidebar above them does. */
function Folding(props: Omit<React.ComponentProps<typeof NavInstances>, "folds" | "onFold">) {
  const [folds, setFolds] = useState<Folds>({})
  return (
    <NavInstances
      {...props}
      folds={folds}
      onFold={(instanceId, open) => setFolds((kept) => ({ ...kept, [instanceId]: open }))}
    />
  )
}

/** Ada's group in the sidebar, listing only her and no instance selected. */
function adaGroup(fields: Partial<InstanceSummary>, threads: ThreadSummary[] = []) {
  const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada", ...fields })
  const hub = fakeHub({ "instance-1": threads })
  const clock = fakeClock()
  render(sidebar(hub, clock, [ada]))
  return { group: groupOf("Ada"), hub, clock }
}

/** An instance's row, the button named by the instance that folds its group. */
function rowOf(name: string) {
  return screen.getByRole("button", { name: new RegExp(`^${name}`) })
}

function groupOf(name: string) {
  const group = rowOf(name).closest("li")
  if (group === null) throw new Error(`${name} is not in a sidebar list`)
  return within(group)
}

/** The browser shows or hides the tab, as switching to another one does. */
function showPage(visibility: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: visibility })
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
  })
}

/** What the row's dot says, or undefined while nothing waits. */
function waiting(group: ReturnType<typeof groupOf>) {
  return group.queryByText(/ waiting for you$/)?.textContent
}

/** The links under a group, by their names. */
function links(group: ReturnType<typeof groupOf>) {
  return group.queryAllByRole("link").map((link) => link.textContent)
}

beforeEach(() => {
  window.history.replaceState(null, "", "/")
  Reflect.deleteProperty(document, "visibilityState")
})

describe("the instance groups", () => {
  it("lists the home instance first, then the others in the hub's order", () => {
    const listed = [
      instanceSummary({ instance_id: "instance-1", persona_name: "Ada" }),
      instanceSummary({ instance_id: "instance-2", manifest_id: "research" }),
      instanceSummary({ instance_id: "instance-3", persona_name: "Kinby", home: true }),
    ]
    render(sidebar(fakeHub({}), fakeClock(), listed))

    const buttons = screen.getAllByRole("button")
    const rows = ["Kinby", "Ada", "research"].map((name) => buttons.indexOf(rowOf(name)))
    expect(rows).toEqual(rows.toSorted((a, b) => a - b))
  })

  it.each([
    ["stopped", {}, "stopped"],
    ["failed", { detail: "exited (1)" }, "stopped"],
    ["missing", {}, "stopped"],
    ["starting", {}, "starting"],
  ] as const)("says a stopped instance whose process is %s is %s", (process, fields, shown) => {
    const { group } = adaGroup({ intended_state: "stopped", process, ...fields })

    expect(group.getByText(shown)).toBeDefined()
  })

  it("says nothing of a running instance's state", async () => {
    const { group, clock } = adaGroup({ process: "running" })
    await act(() => clock.advance(0))

    expect(screen.getByRole("button", { name: "Ada" })).toBeDefined()
    expect(group.queryByText("running")).toBeNull()
  })

  it("lists nothing under a stopped instance, and opens no connection to it", async () => {
    const { group, hub, clock } = adaGroup({ intended_state: "stopped", process: "stopped" })
    await act(() => clock.advance(0))

    expect(links(group)).toEqual([])
    expect(hub.opened).toEqual([])
  })
})

describe("an instance's dot", () => {
  it("counts its threads awaiting approval, awaiting a reply or failed", async () => {
    const { group } = adaGroup({ process: "running" }, [
      thread({ id: "t1", status: "idle" }),
      thread({ id: "t2", status: "running" }),
      thread({ id: "t3", status: "awaiting_approval" }),
      thread({ id: "t4", status: "failed" }),
      thread({ id: "t5", status: "awaiting_approval" }),
      thread({ id: "t6", status: "awaiting_reply" }),
    ])

    await group.findByText(/ waiting for you$/)
    expect(waiting(group)).toBe("4 waiting for you")
  })

  it("counts its factory runs that need a human with its threads", async () => {
    const { group } = adaGroup({ process: "running", needs_human: 2 }, [
      thread({ id: "t1", status: "failed" }),
    ])

    await group.findByText("3 waiting for you")
  })

  it("counts its factory runs alone while it is not running", () => {
    const { group, hub } = adaGroup({
      intended_state: "stopped",
      process: "stopped",
      needs_human: 2,
    })

    expect(waiting(group)).toBe("2 waiting for you")
    expect(hub.opened).toEqual([])
  })

  it("is not shown while nothing waits", async () => {
    const { group, clock } = adaGroup({ process: "running" }, [thread({ id: "t1" })])
    await act(() => clock.advance(0))

    expect(waiting(group)).toBeUndefined()
  })

  it("counts again every 5 seconds while the page is visible, and not while hidden", async () => {
    const { group, hub, clock } = adaGroup({ process: "running" }, [])
    await act(() => clock.advance(0))
    expect(waiting(group)).toBeUndefined()

    hub.threads["instance-1"] = [thread({ id: "t1", status: "failed" })]
    await act(() => clock.advance(4_000))
    expect(waiting(group)).toBeUndefined()
    await act(() => clock.advance(1_000))
    expect(waiting(group)).toBe("1 waiting for you")

    showPage("hidden")
    hub.threads["instance-1"] = []
    await act(() => clock.advance(60_000))
    expect(waiting(group)).toBe("1 waiting for you")

    showPage("visible")
    await act(() => clock.advance(5_000))
    expect(waiting(group)).toBeUndefined()
  })
})

describe("folding an instance's group", () => {
  const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada" })
  const kinby = instanceSummary({ instance_id: "instance-2", persona_name: "Kinby", home: true })
  const grace = instanceSummary({ instance_id: "instance-3", persona_name: "Grace" })

  /** The sidebar listing Kinby, at home, Ada, and Grace, whose threads are `threads`. */
  function groups(threads: Record<string, ThreadSummary[]> = {}, selected?: InstanceSummary) {
    const hub = fakeHub(threads)
    const clock = fakeClock()
    render(sidebar(hub, clock, [ada, kinby, grace], selected))
    return { hub, clock }
  }

  const expanded = (name: string) => rowOf(name).getAttribute("aria-expanded")

  it("starts unfolded at home, and folded elsewhere while nothing waits", async () => {
    const { clock } = groups()
    await act(() => clock.advance(0))

    expect(expanded("Kinby")).toBe("true")
    expect(expanded("Ada")).toBe("false")
    expect(expanded("Grace")).toBe("false")
  })

  it("starts unfolded with something waiting", async () => {
    groups({ "instance-1": [thread({ id: "t1", title: "Deploy notes", status: "failed" })] })

    expect(await groupOf("Ada").findByRole("link", { name: "Deploy notes" })).toBeDefined()
    expect(expanded("Ada")).toBe("true")
    expect(expanded("Grace")).toBe("false")
  })

  it("starts unfolded when it holds the open page", async () => {
    window.history.replaceState(null, "", "/instances/instance-3/stats")
    const { clock } = groups({}, grace)
    await act(() => clock.advance(0))

    expect(expanded("Grace")).toBe("true")
    expect(expanded("Ada")).toBe("false")
  })

  it("folds and unfolds on a click of its row, which opens nothing", async () => {
    const { clock } = groups({ "instance-2": [thread({ id: "t1", title: "Deploy notes" })] })
    const user = userEvent.setup()
    expect(await groupOf("Kinby").findByRole("link", { name: "Deploy notes" })).toBeDefined()

    await user.click(rowOf("Kinby"))
    expect(expanded("Kinby")).toBe("false")
    expect(groupOf("Kinby").queryByRole("link", { name: "Deploy notes" })).toBeNull()

    await user.click(rowOf("Ada"))
    expect(expanded("Ada")).toBe("true")
    expect(window.location.pathname).toBe("/")
    await act(() => clock.advance(0))
  })

  it("stays as the user folded it when something starts waiting", async () => {
    const { hub, clock } = groups()
    const user = userEvent.setup()
    await act(() => clock.advance(0))
    await user.click(rowOf("Kinby"))

    hub.threads["instance-2"] = [thread({ id: "t1", status: "failed" })]
    await act(() => clock.advance(5_000))

    expect(waiting(groupOf("Kinby"))).toBe("1 waiting for you")
    expect(expanded("Kinby")).toBe("false")
  })
})

describe("a running instance's group", () => {
  const board = { id: "applications", title: "Job board", updated_at: "2026-10-25T09:30:00Z" }
  const reading = { id: "reading", title: "Reading list", updated_at: "2026-10-24T08:00:00Z" }
  const kinby = instanceSummary({ instance_id: "instance-1", persona_name: "Kinby", home: true })

  /** The sidebar listing Kinby, at home and running with `threads` and `views`. */
  function kinbyGroup(
    threads: ThreadSummary[],
    views: ViewSummary[] = [],
    selectedClient?: InstanceClient,
  ) {
    const hub = fakeHub({ "instance-1": threads }, { "instance-1": views })
    const clock = fakeClock()
    const selected = selectedClient === undefined ? undefined : kinby
    render(sidebar(hub, clock, [kinby], selected, selectedClient))
    return { hub, clock, group: groupOf("Kinby") }
  }

  it("lists its views, its 4 most recent sidebar threads, then All threads", async () => {
    const { group } = kinbyGroup(
      [
        thread({ id: "t1", title: "Deploy notes" }),
        thread({ id: "t2", title: "Old plan", archived: true }),
        thread({ id: "t3", title: "Review" }),
        thread({ id: "t4", title: "Groceries" }),
        thread({ id: "t5", title: "Taxes" }),
        thread({ id: "t6", title: "Holidays" }),
      ],
      [board, reading],
    )

    await group.findByRole("link", { name: "Deploy notes" })
    await group.findByRole("link", { name: "Job board" })
    expect(links(group)).toEqual([
      "Job board",
      "Reading list",
      "Deploy notes",
      "Review",
      "Groceries",
      "Taxes",
      "All threads",
    ])
  })

  it("names each thread by its title and describes it by its status", async () => {
    const { group } = kinbyGroup([
      thread({ id: "t1", title: "Deploy notes", status: "running" }),
      thread({ id: "t2", title: "Pull request review", status: "awaiting_approval" }),
      thread({ id: "t3", title: "Nightly digest", status: "failed" }),
      thread({ id: "t4" }),
    ])

    await group.findByRole("link", { name: "Deploy notes" })
    const description = (name: string) => {
      const id = group.getByRole("link", { name }).getAttribute("aria-describedby")
      return id === null ? undefined : document.getElementById(id)?.textContent
    }
    expect(description("Deploy notes")).toBe("Working")
    expect(description("Pull request review")).toBe("Needs you")
    expect(description("Nightly digest")).toBe("Failed")
    expect(description("Untitled thread")).toBeUndefined()
  })

  it("opens a thread, a view and the thread list page at their paths", async () => {
    const { group } = kinbyGroup([thread({ id: "t1", title: "Deploy notes" })], [board])
    const user = userEvent.setup()

    await user.click(await group.findByRole("link", { name: "Deploy notes" }))
    expect(window.location.pathname).toBe("/instances/instance-1/threads/t1")

    await user.click(await group.findByRole("link", { name: "Job board" }))
    expect(window.location.pathname).toBe("/instances/instance-1/views/applications")

    await user.click(group.getByRole("link", { name: "All threads" }))
    expect(window.location.pathname).toBe("/instances/instance-1/threads")
  })

  it("lists a view a turn created on the next poll", async () => {
    const { hub, clock, group } = kinbyGroup([])
    await act(() => clock.advance(0))
    expect(group.queryByRole("link", { name: "Job board" })).toBeNull()

    hub.views["instance-1"] = [board]
    await act(() => clock.advance(5_000))

    expect(group.getByRole("link", { name: "Job board" })).toBeDefined()
  })

  it("lists the selected instance on its connection, marking the open thread", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/threads/t2")
    const selectedClient = fakeHub({
      "instance-1": [
        thread({ id: "t1", title: "Deploy notes" }),
        thread({ id: "t2", title: "Review" }),
      ],
    }).instance("instance-1")
    const { hub, group } = kinbyGroup([], [], selectedClient)

    const open = await group.findByRole("link", { name: "Review" })

    expect(open.getAttribute("aria-current")).toBe("page")
    expect(
      group.getByRole("link", { name: "Deploy notes" }).getAttribute("aria-current"),
    ).toBeNull()
    expect(hub.opened).toEqual([])
  })

  it("marks the open view", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/views/applications")
    const selectedClient = fakeHub({}, { "instance-1": [board, reading] }).instance("instance-1")
    const { group } = kinbyGroup([], [], selectedClient)

    const open = await group.findByRole("link", { name: "Job board" })

    expect(open.getAttribute("aria-current")).toBe("page")
    expect(
      group.getByRole("link", { name: "Reading list" }).getAttribute("aria-current"),
    ).toBeNull()
  })

  it("shows what the open thread listed on the selected connection without waiting for a poll", async () => {
    let title = "Deploy notes"
    const selectedClient = {
      ...stubCaller({
        "thread.list": () => ({
          threads: [thread({ id: "t1", title, status: "failed" })],
          ceiling: "full-access",
          cursor: null,
        }),
        "view.list": () => ({ views: [] }),
      }),
      state: () => "connected" as const,
      onStateChange: () => () => {},
    }
    const hub = fakeHub({})
    render(sidebar(hub, fakeClock(), [kinby], kinby, selectedClient))
    const group = groupOf("Kinby")
    expect(await group.findByRole("link", { name: "Deploy notes" })).toBeDefined()

    title = "Release plan"
    await act(() => threadList(selectedClient, "sidebar").listKept())

    expect(group.getByRole("link", { name: "Release plan" })).toBeDefined()
    expect(waiting(group)).toBe("1 waiting for you")
    expect(hub.opened).toEqual([])
  })

  it("closes its own connection to an instance once that instance is selected", async () => {
    const hub = fakeHub({})
    const clock = fakeClock()
    const { rerender } = render(sidebar(hub, clock, [kinby]))
    await act(() => clock.advance(0))
    expect(hub.opened).toEqual(["instance-1"])

    rerender(sidebar(hub, clock, [kinby], kinby, fakeHub({}).instance("instance-1")))

    expect(hub.closed).toEqual(["instance-1"])
  })
})

describe("an instance's row menu", () => {
  const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada" })

  async function openMenu(instance = ada) {
    render(sidebar(fakeHub({}), fakeClock(), [instance]))
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Menu for Ada" }))
    return user
  }

  it("starts a thread with the instance, then opens each of its tabs", async () => {
    await openMenu()

    expect((await screen.findAllByRole("menuitem")).map((item) => item.textContent)).toEqual([
      "New thread with Ada",
      "Overview",
      "Config",
      "Routines",
      "Memory",
      "Stats",
    ])
  })

  it("opens the home screen sending to the instance", async () => {
    const user = await openMenu()

    await user.click(await screen.findByRole("menuitem", { name: "New thread with Ada" }))

    expect(window.location.pathname).toBe("/")
    expect(window.location.search).toBe("?to=instance-1")
  })

  it("offers no new thread with an instance that is not running", async () => {
    const user = await openMenu({ ...ada, intended_state: "stopped", process: "stopped" })

    const newThread = await screen.findByRole("menuitem", { name: "New thread with Ada" })
    expect(newThread.getAttribute("aria-disabled")).toBe("true")
    await user.click(newThread)
    expect(window.location.search).toBe("")
  })

  it.each([
    ["Overview", "/instances/instance-1"],
    ["Config", "/instances/instance-1/config"],
    ["Routines", "/instances/instance-1/routines"],
    ["Memory", "/instances/instance-1/memory"],
    ["Stats", "/instances/instance-1/stats"],
  ])("opens %s at its path", async (tab, path) => {
    const user = await openMenu()

    await user.click(await screen.findByRole("menuitem", { name: tab }))

    expect(window.location.pathname).toBe(path)
  })
})
