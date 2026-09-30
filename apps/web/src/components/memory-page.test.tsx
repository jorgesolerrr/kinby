import type {
  ConfigChange,
  MemoryListCommand,
  MemoryListResult,
  MemoryOpenResult,
  NodeSummary,
  ProfileResult,
  ThreadSummary,
} from "@kinby/contract"
import { CallError } from "@kinby/contract"
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

const called = (caller: ReturnType<typeof stubCaller>, method: string) =>
  caller.calls.filter((call) => call.method === method).map((call) => call.params)

const refused = (code: "NOT_FOUND" | "INVALID_ARGUMENT", fields: Record<string, string> = {}) =>
  new CallError({ code, message: `Refused with ${code}.`, retryable: false, fields })

const tea = summary({
  node: "2026-09-28-0192c3a4-likes-green-tea",
  description: "Likes green tea",
  date: "2026-09-28",
  subjects: ["drinks", "mornings"],
  source: "user",
})

/** A page whose list gains `tea` once a write adds it. */
function openWritablePage(answers: Answers = {}) {
  let written = false
  const page = openPage({
    "memory.list": () =>
      listing(written ? [tea, deploy, coffee, markdown] : [deploy, coffee, markdown]),
    "memory.open": ({ node }) => {
      const found = node === tea.node ? opened(tea) : OPENED[node]
      if (found === undefined) throw new Error(`No node ${node}`)
      return found
    },
    "memory.add": () => {
      written = true
      return { node: tea.node }
    },
    "memory.correct": () => {
      written = true
      return { node: tea.node }
    },
    "memory.forget": () => ({}),
    ...answers,
  })
  return page
}

const EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
const profile: ProfileResult = { text: "Call me Jo.", hash: "hash-1", tokens: 3 }

const byYou: ConfigChange = {
  // Local time, so the page shows the same clock time wherever the test runs.
  at: new Date(2026, 8, 28, 10, 4).toISOString(),
  file: "memory/profile.md",
  actor: "app",
  thread_id: null,
  turn_id: null,
  diff: "",
}

/** The page with its Profile tab open. The profile was last changed by the user. */
async function openProfile(answers: Answers = {}) {
  const page = openPage({
    "profile.get": () => profile,
    "config.history": ({ file }) => ({ changes: file === "memory/profile.md" ? [byYou] : [] }),
    ...answers,
  })
  await page.user.click(await screen.findByRole("tab", { name: "Profile" }))
  return page
}

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

  it("left-aligns every line of a node row, since a button centers its text", async () => {
    openPage()

    const nodes = await screen.findByRole("list", { name: "Nodes" })
    // jsdom applies no Tailwind, so the class stands in for the computed alignment.
    expect(
      within(nodes)
        .getAllByRole("button")
        .map((button) => button.classList.contains("text-left")),
    ).toEqual([true, true, true])
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

  it("edits the profile with an approximate token count and says who changed it last", async () => {
    const { caller, user } = await openProfile({
      "profile.set": ({ text }) => ({ text, hash: "hash-2", tokens: 7 }),
    })

    const editor = await screen.findByRole("textbox", { name: "memory/profile.md" })
    expect(editor).toHaveProperty("value", "Call me Jo.")
    expect(screen.getByText(/about 3 tokens/)).toBeDefined()
    expect(screen.getByText("Last changed by you, Sep 28, 2026, 10:04 AM")).toBeDefined()
    await user.type(editor, " Mornings only.")
    expect(screen.getByText(/about 7 tokens/)).toBeDefined()
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Saved. It applies at the next turn.")).toBeDefined()
    expect(called(caller, "profile.set")).toEqual([
      { text: "Call me Jo. Mornings only.", hash: "hash-1" },
    ])
    expect(called(caller, "config.history")).toEqual([
      { file: "memory/profile.md", limit: 1 },
      { file: "memory/profile.md", limit: 1 },
    ])
    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true)
  })

  it("counts the profile's tokens as the instance does, by characters", async () => {
    const { user } = await openProfile({
      "profile.get": () => ({ text: "", hash: EMPTY, tokens: 0 }),
    })

    const editor = await screen.findByRole("textbox", { name: "memory/profile.md" })
    expect(screen.getByText(/about 0 tokens/)).toBeDefined()
    await user.type(editor, "🌱🌱🌱🌱🌱")

    expect(screen.getByText(/about 2 tokens/)).toBeDefined()
  })

  it("offers to load theirs when the profile changed since it was opened", async () => {
    let theirs = false
    const { caller, user } = await openProfile({
      "profile.get": () =>
        theirs ? { text: "Call me Jorge.", hash: "hash-3", tokens: 4 } : profile,
      "profile.set": () => {
        theirs = true
        throw new CallError({ code: "STALE", message: "profile.md changed.", retryable: false })
      },
    })

    const editor = await screen.findByRole("textbox", { name: "memory/profile.md" })
    await user.type(editor, " Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

    expect(await screen.findByDisplayValue("Call me Jorge.")).toBe(editor)
    expect(screen.getByText(/about 4 tokens/)).toBeDefined()
    expect(screen.queryByRole("alert")).toBeNull()
    await user.type(editor, " Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(called(caller, "profile.set").at(-1)).toEqual({
      text: "Call me Jorge. Mine.",
      hash: "hash-3",
    })
  })

  it("adds a fact from the pane with nothing open, then reloads the list and opens it", async () => {
    const { caller, user } = openWritablePage()
    await nodeButton("Picked markdown")

    await user.type(pane().getByLabelText("Description"), "Likes green tea")
    await user.type(pane().getByLabelText("Subjects"), "drinks, mornings,")
    await user.type(pane().getByLabelText("Body"), "No milk.")
    await user.click(pane().getByRole("button", { name: "Add fact" }))

    expect(called(caller, "memory.add")).toEqual([
      { description: "Likes green tea", subjects: ["drinks", "mornings"], body: "No milk." },
    ])
    expect(await pane().findByRole("heading", { name: "Likes green tea" })).toBeDefined()
    expect(listCalls(caller)).toHaveLength(2)
    expect(await nodeButton("Likes green tea")).toBeDefined()
  })

  it("corrects an open fact from a copy of its fields and opens the new fact", async () => {
    const { caller, user } = openWritablePage()
    await user.click(await nodeButton("Picked markdown"))

    await user.click(await pane().findByRole("button", { name: "Correct" }))
    const description = pane().getByLabelText("Description")
    expect(description).toHaveProperty("value", "Picked markdown")
    expect(pane().getByLabelText("Subjects")).toHaveProperty("value", "memory, kinby")
    await user.clear(description)
    await user.type(description, "Likes green tea")
    await user.click(pane().getByRole("button", { name: "Save correction" }))

    expect(called(caller, "memory.correct")).toEqual([
      {
        node: markdown.node,
        description: "Likes green tea",
        subjects: ["memory", "kinby"],
        body: "The body of Picked markdown.",
      },
    ])
    expect(await pane().findByRole("heading", { name: "Likes green tea" })).toBeDefined()
    expect(listCalls(caller)).toHaveLength(2)
  })

  it("drops a correction on Cancel and shows the fact again", async () => {
    const { caller, user } = openWritablePage()
    await user.click(await nodeButton("Picked markdown"))

    await user.click(await pane().findByRole("button", { name: "Correct" }))
    await user.click(pane().getByRole("button", { name: "Cancel" }))

    expect(pane().getByRole("heading", { name: "Picked markdown" })).toBeDefined()
    expect(called(caller, "memory.correct")).toEqual([])
  })

  it("offers only Forget on an open episode", async () => {
    const { user } = openWritablePage()

    await user.click(await nodeButton("Fixed the deploy"))

    expect(await pane().findByRole("button", { name: "Forget" })).toBeDefined()
    expect(pane().queryByRole("button", { name: "Correct" })).toBeNull()
  })

  it("forgets only after a confirmation that says it can't be undone, then closes the pane", async () => {
    const { caller, user } = openWritablePage()
    await user.click(await nodeButton("Fixed the deploy"))

    await user.click(await pane().findByRole("button", { name: "Forget" }))
    let dialog = await screen.findByRole("alertdialog", { name: "Forget this episode?" })
    expect(dialog.textContent).toContain("can't be undone")
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    expect(called(caller, "memory.forget")).toEqual([])
    await user.click(pane().getByRole("button", { name: "Forget" }))
    dialog = await screen.findByRole("alertdialog", { name: "Forget this episode?" })
    await user.click(within(dialog).getByRole("button", { name: "Forget" }))

    expect(called(caller, "memory.forget")).toEqual([{ node: deploy.node }])
    expect(await pane().findByRole("button", { name: "Add fact" })).toBeDefined()
    expect(listCalls(caller)).toHaveLength(2)
  })

  it.each([
    [
      "a correction",
      async (user: ReturnType<typeof userEvent.setup>) => {
        await user.click(await pane().findByRole("button", { name: "Correct" }))
        await user.click(pane().getByRole("button", { name: "Save correction" }))
      },
    ],
    [
      "a forget",
      async (user: ReturnType<typeof userEvent.setup>) => {
        await user.click(await pane().findByRole("button", { name: "Forget" }))
        const dialog = await screen.findByRole("alertdialog", { name: "Forget this fact?" })
        await user.click(within(dialog).getByRole("button", { name: "Forget" }))
      },
    ],
  ])("says the node is gone and reloads the list when %s finds it gone", async (_, write) => {
    const gone = () => {
      throw refused("NOT_FOUND")
    }
    const { caller, user } = openWritablePage({ "memory.correct": gone, "memory.forget": gone })
    await user.click(await nodeButton("Picked markdown"))

    await write(user)

    expect(await pane().findByText(/is gone/)).toBeDefined()
    expect(pane().getByRole("button", { name: "Add fact" })).toBeDefined()
    expect(listCalls(caller)).toHaveLength(2)
  })

  it("shows each field's error beside the field the instance refused", async () => {
    const { user } = openWritablePage({
      "memory.add": () => {
        throw refused("INVALID_ARGUMENT", { description: "Describe the fact." })
      },
    })
    await nodeButton("Picked markdown")

    await user.type(pane().getByLabelText("Description"), " ")
    await user.click(pane().getByRole("button", { name: "Add fact" }))

    expect(await pane().findByText("Describe the fact.")).toBeDefined()
    expect(pane().getByLabelText("Description").getAttribute("aria-invalid")).toBe("true")
    expect(pane().getByLabelText("Subjects").getAttribute("aria-invalid")).toBeNull()
  })

  it("closes an open node to offer Add fact again", async () => {
    const { user } = openWritablePage()
    await user.click(await nodeButton("Picked markdown"))

    await user.click(await pane().findByRole("button", { name: "Close" }))

    expect(pane().getByRole("button", { name: "Add fact" })).toBeDefined()
  })
})
