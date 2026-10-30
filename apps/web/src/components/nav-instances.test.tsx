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
import { beforeEach, describe, expect, it } from "vitest"

import { NavInstances } from "@/components/nav-instances"
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
      <NavInstances
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

/** Ada's row in the sidebar, listing only her and no instance selected. */
function adaRow(fields: Partial<InstanceSummary>, threads: ThreadSummary[] = []) {
  const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada", ...fields })
  const hub = fakeHub({ "instance-1": threads })
  const clock = fakeClock()
  render(sidebar(hub, clock, [ada]))
  return { row: rowOf("Ada"), hub, clock }
}

function rowOf(name: string) {
  const link = screen.getByRole("link", { name })
  const row = screen.getAllByRole("listitem").find((item) => item.contains(link))
  return within(row!)
}

/** The browser shows or hides the tab, as switching to another one does. */
function showPage(visibility: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: visibility })
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
  })
}

/** What the row's needs-you count reads, the description of its link. */
function needsYou(row: ReturnType<typeof rowOf>) {
  return row.queryByText(/ waiting for you$/)?.textContent
}

describe("the instances in the sidebar", () => {
  it("lists the home instance first, then the others in the hub's order", () => {
    const listed = [
      instanceSummary({ instance_id: "instance-1", persona_name: "Ada" }),
      instanceSummary({ instance_id: "instance-2", manifest_id: "research" }),
      instanceSummary({ instance_id: "instance-3", persona_name: "Kinby", home: true }),
    ]
    render(sidebar(fakeHub({}), fakeClock(), listed))

    const links = screen.getAllByRole("link")
    const named = ["Kinby", "Ada", "research"].map((name) => screen.getByRole("link", { name }))
    expect(named.map((link) => links.indexOf(link))).toEqual([0, 1, 2])
  })
})

describe("an instance's badge in the sidebar", () => {
  it.each([
    ["stopped", {}, "stopped"],
    ["failed", { detail: "exited (1)" }, "stopped"],
    ["created", {}, "stopped"],
    ["missing", {}, "stopped"],
    ["starting", {}, "starting"],
    ["unavailable", {}, "unavailable"],
  ] as const)("reads a stopped instance whose process is %s as %s", (process, fields, shown) => {
    const { row } = adaRow({ intended_state: "stopped", process, ...fields })

    expect(row.getByText(shown)).toBeDefined()
  })

  it.each([
    ["failed", { process: "failed", detail: "exited (1)" }, "failed"],
    ["created", { process: "created" }, "created"],
    ["missing", { process: "missing" }, "missing"],
    ["restarting", { process: "starting", detail: "restarting" }, "restarting"],
    ["running", { process: "running" }, "running"],
  ] as const)("reads an instance meant to run that is %s as %s", (_, fields, shown) => {
    const { row } = adaRow({ intended_state: "running", ...fields })

    expect(row.getByText(shown)).toBeDefined()
  })
})

describe("the needs-you count on an instance that is not selected", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("counts its threads awaiting approval, awaiting a reply or failed, in place of its state", async () => {
    const { row } = adaRow({ process: "running" }, [
      thread({ id: "t1", status: "idle" }),
      thread({ id: "t2", status: "running" }),
      thread({ id: "t3", status: "awaiting_approval" }),
      thread({ id: "t4", status: "failed" }),
      thread({ id: "t5", status: "awaiting_approval" }),
      thread({ id: "t6", status: "awaiting_reply" }),
    ])

    await row.findByText(/ waiting for you$/)
    expect(needsYou(row)).toBe("4 waiting for you")
    expect(row.queryByText("running")).toBeNull()
  })

  it("counts an archived thread awaiting approval, and not an archived failed one", async () => {
    const { row } = adaRow({ process: "running" }, [
      thread({ id: "t1", status: "awaiting_approval", archived: true }),
      thread({ id: "t2", status: "failed", archived: true }),
    ])

    await row.findByText(/ waiting for you$/)
    expect(needsYou(row)).toBe("1 waiting for you")
  })

  it("counts its factory runs that need a human with its threads", async () => {
    const { row } = adaRow({ process: "running", needs_human: 2 }, [
      thread({ id: "t1", status: "failed" }),
    ])

    await row.findByText(/ waiting for you$/)
    expect(needsYou(row)).toBe("3 waiting for you")
  })

  it("counts its factory runs that need a human while it is not running", async () => {
    const { row, hub } = adaRow({ process: "stopped", needs_human: 2 }, [])

    expect(needsYou(row)).toBe("2 waiting for you")
    expect(hub.opened).toEqual([])
  })

  it("shows its state when no thread needs you", async () => {
    const { row, clock } = adaRow({ process: "running" }, [
      thread({ id: "t1", status: "idle" }),
      thread({ id: "t2", status: "running" }),
    ])

    await act(() => clock.advance(0))

    expect(row.getByText("running")).toBeDefined()
    expect(needsYou(row)).toBeUndefined()
  })

  it("selects the instance when its row is clicked", async () => {
    const { row } = adaRow({ process: "running" }, [
      thread({ id: "t1", status: "awaiting_approval" }),
    ])
    await row.findByText(/ waiting for you$/)

    await userEvent.setup().click(screen.getByRole("link", { name: "Ada" }))

    expect(window.location.pathname).toBe("/instances/instance-1")
  })

  it("counts again every 5 seconds while the page is visible, and not while hidden", async () => {
    const { row, hub, clock } = adaRow({ process: "running" }, [])
    await act(() => clock.advance(0))
    expect(row.getByText("running")).toBeDefined()

    hub.threads["instance-1"] = [thread({ id: "t1", status: "failed" })]
    await act(() => clock.advance(4_000))
    expect(row.getByText("running")).toBeDefined()
    await act(() => clock.advance(1_000))
    expect(needsYou(row)).toBe("1 waiting for you")

    showPage("hidden")
    hub.threads["instance-1"] = []
    await act(() => clock.advance(60_000))
    expect(needsYou(row)).toBe("1 waiting for you")

    showPage("visible")
    await act(() => clock.advance(5_000))
    expect(row.getByText("running")).toBeDefined()
  })

  it("reaches neither the selected instance nor one that is not running", async () => {
    const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada" })
    const grace = instanceSummary({
      instance_id: "instance-2",
      persona_name: "Grace",
      intended_state: "stopped",
      process: "stopped",
    })
    const hub = fakeHub({})
    const clock = fakeClock()

    render(sidebar(hub, clock, [ada, grace], ada))
    await act(() => clock.advance(0))

    expect(hub.opened).toEqual([])
    expect(rowOf("Grace").getByText("stopped")).toBeDefined()
  })

  it("closes its connection to an instance once that instance is selected", async () => {
    const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada" })
    const hub = fakeHub({})
    const clock = fakeClock()
    const { rerender } = render(sidebar(hub, clock, [ada]))
    await act(() => clock.advance(0))
    expect(hub.opened).toEqual(["instance-1"])

    rerender(sidebar(hub, clock, [ada], ada))

    expect(hub.closed).toEqual(["instance-1"])
  })

  it("counts the selected instance's threads from the list its threads column keeps", async () => {
    const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada", needs_human: 1 })
    const hub = fakeHub({})
    const selectedClient = {
      ...stubCaller({
        "thread.list": () => ({
          threads: [thread({ id: "t1", status: "failed" }), thread({ id: "t2" })],
          ceiling: "full-access",
          cursor: null,
        }),
        "view.list": () => ({ views: [] }),
      }),
      state: () => "connected" as const,
      onStateChange: () => () => {},
    }
    render(sidebar(hub, fakeClock(), [ada], ada, selectedClient))
    expect(needsYou(rowOf("Ada"))).toBe("1 waiting for you")

    await act(() => threadList(selectedClient, "sidebar").list())

    expect(needsYou(rowOf("Ada"))).toBe("2 waiting for you")
    expect(hub.opened).toEqual([])
  })
})

describe("an instance's views in the sidebar", () => {
  const board = { id: "applications", title: "Job board", updated_at: "2026-10-25T09:30:00Z" }
  const reading = { id: "reading", title: "Reading list", updated_at: "2026-10-24T08:00:00Z" }

  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  /** The sidebar listing Ada, running with `views`, and Grace, stopped. */
  function withViews(views: ViewSummary[], selectedClient?: InstanceClient) {
    const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada" })
    const grace = instanceSummary({
      instance_id: "instance-2",
      persona_name: "Grace",
      intended_state: "stopped",
      process: "stopped",
    })
    const hub = fakeHub({}, { "instance-1": views })
    const clock = fakeClock()
    const selected = selectedClient === undefined ? undefined : ada
    render(sidebar(hub, clock, [ada, grace], selected, selectedClient))
    return { hub, clock }
  }

  it("lists a running instance's views under it, each opening its view page", async () => {
    withViews([board, reading])

    const ada = rowOf("Ada")
    await ada.findByRole("link", { name: "Job board" })
    const links = ada.getAllByRole("link").map((link) => link.textContent)
    expect(links.slice(1)).toEqual(["Job board", "Reading list"])
    expect(rowOf("Grace").getAllByRole("link")).toHaveLength(1)

    await userEvent.setup().click(ada.getByRole("link", { name: "Reading list" }))

    expect(window.location.pathname).toBe("/instances/instance-1/views/reading")
  })

  it("lists a view a turn created on the next poll", async () => {
    const { hub, clock } = withViews([])
    await act(() => clock.advance(0))
    expect(rowOf("Ada").queryByRole("link", { name: "Job board" })).toBeNull()

    hub.views["instance-1"] = [board]
    await act(() => clock.advance(5_000))

    expect(rowOf("Ada").getByRole("link", { name: "Job board" })).toBeDefined()
  })

  it("lists the selected instance's views on its connection, marking the one open", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/views/applications")
    const selectedClient = fakeHub({}, { "instance-1": [board, reading] }).instance("instance-1")
    const { hub } = withViews([], selectedClient)

    const open = await rowOf("Ada").findByRole("link", { name: "Job board" })

    expect(open.getAttribute("aria-current")).toBe("page")
    expect(
      rowOf("Ada").getByRole("link", { name: "Reading list" }).getAttribute("aria-current"),
    ).toBeNull()
    expect(hub.opened).toEqual([])
  })
})
