import type {
  MemoryListCommand,
  MemoryListResult,
  MemoryOpenResult,
  NodeSummary,
  ThreadSummary,
} from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { MemoryPage } from "@/components/memory-page"

const THREAD = "11111111-1111-1111-1111-111111111111"

function summary(fields: Pick<NodeSummary, "node" | "description"> & Partial<NodeSummary>) {
  return {
    kind: "fact",
    date: "2026-09-01",
    subjects: ["kinby"],
    source: "agent",
    ...fields,
  } satisfies NodeSummary
}

const deploy = summary({
  node: "2026-09-03-fixed-deploy",
  description: "Fixed the deploy",
  kind: "episode",
  date: "2026-09-03",
  subjects: ["kinby", "deploy"],
  source: "recap",
})
const coffee = summary({
  node: "2026-09-02-likes-coffee",
  description: "Likes coffee",
  date: "2026-09-02",
  subjects: ["coffee"],
  source: "user",
})
const markdown = summary({
  node: "2026-09-01-picked-markdown",
  description: "Picked markdown",
  subjects: ["memory", "kinby"],
})

function opened(node: NodeSummary, fields: Partial<MemoryOpenResult> = {}): MemoryOpenResult {
  return {
    ...node,
    body: `The body of ${node.description}.`,
    thread: node.source === "user" ? null : THREAD,
    turn: null,
    tools: null,
    ...fields,
  }
}

const OPENED: Record<string, MemoryOpenResult> = {
  [deploy.node]: opened(deploy, {
    turn: "22222222-2222-2222-2222-222222222222",
    tools: ["grep", "bash"],
  }),
  [coffee.node]: opened(coffee),
  [markdown.node]: opened(markdown),
}

const thread: ThreadSummary = {
  id: THREAD,
  title: "Deploy day",
  created_at: "2026-09-01T10:00:00Z",
  last_activity_at: "2026-09-01T10:00:00Z",
  mode: "ask",
  mode_pinned: false,
  status: "idle",
}

function listing(items: NodeSummary[], cursor: string | null = null): MemoryListResult {
  return { items, cursor }
}

function openPage(answers: Answers = {}) {
  const caller = stubCaller({
    "memory.list": () => listing([deploy, coffee, markdown]),
    "memory.open": ({ node }) => {
      const found = OPENED[node]
      if (found === undefined) throw new Error(`No node ${node}`)
      return found
    },
    "thread.list": () => ({ threads: [thread], ceiling: "full-access" }),
    ...answers,
  })
  render(<MemoryPage client={caller} clock={fakeClock()} instanceId="instance-1" />)
  return { caller, user: userEvent.setup() }
}

const listCalls = (caller: ReturnType<typeof stubCaller>) =>
  caller.calls
    .filter((call) => call.method === "memory.list")
    .map((call) => call.params as MemoryListCommand)

const nodeButton = (description: string) =>
  screen.findByRole("button", { name: new RegExp(description) })

const pane = () => within(screen.getByRole("region", { name: "Opened node" }))

describe("the memory page", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/instances/instance-1/memory")
  })

  it("lists the knowledge graph as the instance orders it and opens a node in the pane", async () => {
    const { user } = openPage()

    const nodes = await screen.findByRole("list", { name: "Nodes" })
    expect(
      within(nodes)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual([
      expect.stringContaining("Fixed the deploy"),
      expect.stringContaining("Likes coffee"),
      expect.stringContaining("Picked markdown"),
    ])
    await user.click(await nodeButton("Picked markdown"))

    expect(await pane().findByText("The body of Picked markdown.")).toBeDefined()
    expect(pane().getByRole("heading", { name: "Picked markdown" })).toBeDefined()
    expect(pane().getByText("2026-09-01")).toBeDefined()
    expect(pane().getByRole("button", { name: "memory" })).toBeDefined()
  })

  it("narrows the list by search, kind, and date bounds", async () => {
    const { caller, user } = openPage()
    await nodeButton("Picked markdown")

    await user.type(screen.getByRole("searchbox", { name: "Search" }), "kinby")
    await user.click(screen.getByRole("button", { name: "Episodes" }))
    await user.type(screen.getByLabelText("After"), "2026-09-01")
    await user.type(screen.getByLabelText("Before"), "2026-09-30")

    expect(listCalls(caller).at(0)).toEqual({ query: "" })
    expect(listCalls(caller).at(-1)).toEqual({
      query: "kinby",
      kind: "episode",
      after: "2026-09-01",
      before: "2026-09-30",
    })
    await user.click(screen.getByRole("button", { name: "All" }))
    expect(listCalls(caller).at(-1)).not.toHaveProperty("kind")
  })

  it("narrows the list to a subject clicked in the pane, until the filter is cleared", async () => {
    const { caller, user } = openPage()
    await user.click(await nodeButton("Fixed the deploy"))

    await user.click(await pane().findByRole("button", { name: "deploy" }))

    expect(listCalls(caller).at(-1)).toEqual({ query: "", subject: "deploy" })
    await user.click(screen.getByRole("button", { name: "Stop filtering by deploy" }))
    expect(listCalls(caller).at(-1)).toEqual({ query: "" })
  })

  it("loads the next page below the last node shown, and stops on the last page", async () => {
    const { caller, user } = openPage({
      "memory.list": ({ cursor }) =>
        cursor === undefined || cursor === null
          ? listing([deploy, coffee], coffee.node)
          : listing([markdown]),
    })

    await user.click(await screen.findByRole("button", { name: "Load more" }))

    expect(await nodeButton("Picked markdown")).toBeDefined()
    expect(await nodeButton("Fixed the deploy")).toBeDefined()
    expect(listCalls(caller).at(-1)).toEqual({ query: "", cursor: coffee.node })
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull()
  })

  it("says what recapped an episode and shows its tool path, linking to its thread", async () => {
    const { user } = openPage()

    await user.click(await nodeButton("Fixed the deploy"))

    expect(await pane().findByText("Tool path: grep → bash")).toBeDefined()
    expect(pane().getByText(/Recap of a turn in/)).toBeDefined()
    await user.click(await pane().findByRole("button", { name: "Deploy day" }))
    expect(window.location.pathname).toBe(`/instances/instance-1/threads/${THREAD}`)
  })

  it("says a fact the agent remembered was remembered in its thread", async () => {
    const { user } = openPage()

    await user.click(await nodeButton("Picked markdown"))

    expect(await pane().findByText(/Remembered in/)).toBeDefined()
    expect(await pane().findByRole("button", { name: "Deploy day" })).toBeDefined()
    expect(pane().queryByText(/Tool path/)).toBeNull()
  })

  it("says a fact the user added was added by them, with no thread", async () => {
    const { user } = openPage()

    await user.click(await nodeButton("Likes coffee"))

    expect(await pane().findByText("Added by you")).toBeDefined()
    expect(pane().queryByText(/Remembered in|Recap of a turn in/)).toBeNull()
    expect(screen.getByRole("button", { name: /Likes coffee/ }).textContent).toContain(
      "added by you",
    )
  })

  it("reads the list and the opened node again on Refresh, and never on its own", async () => {
    const clock = fakeClock()
    const caller = stubCaller({
      "memory.list": () => listing([markdown]),
      "memory.open": () => OPENED[markdown.node] as MemoryOpenResult,
      "thread.list": () => ({ threads: [thread], ceiling: "full-access" }),
    })
    render(<MemoryPage client={caller} clock={clock} instanceId="instance-1" />)
    const user = userEvent.setup()
    await user.click(await nodeButton("Picked markdown"))
    await pane().findByText("The body of Picked markdown.")
    const reads = () => caller.calls.filter((call) => call.method.startsWith("memory.")).length

    await act(() => clock.advance(10 * 60_000))
    expect(reads()).toBe(2)
    await user.click(screen.getByRole("button", { name: "Refresh" }))

    await pane().findByText("The body of Picked markdown.")
    expect(caller.calls.filter((call) => call.method === "memory.list")).toHaveLength(2)
    expect(caller.calls.filter((call) => call.method === "memory.open")).toHaveLength(2)
  })

  it("shows the profile tab as not available yet", async () => {
    openPage()

    const profile = await screen.findByRole("tab", { name: "Profile" })

    expect(profile.getAttribute("aria-disabled")).toBe("true")
    expect(screen.getByRole("tab", { name: "Knowledge graph" }).getAttribute("aria-selected")).toBe(
      "true",
    )
  })
})
