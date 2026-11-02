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
import { StrictMode } from "react"
import { beforeEach, describe, expect, it } from "vitest"

import { MemoryPage } from "@/components/memory-page"
import { PROFILE_FILE } from "@/lib/profile"

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
    run: null,
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
  archived: false,
  origin: { kind: "user" },
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
    "thread.list": () => ({ threads: [thread], ceiling: "full-access", cursor: null }),
    "profile.get": () => profile,
    "config.history": ({ file }) => ({ changes: file === PROFILE_FILE ? [byYou] : [] }),
    ...answers,
  })
  render(<MemoryPage client={caller} clock={fakeClock()} instanceId="instance-1" />)
  return { caller, user: userEvent.setup() }
}

const listCalls = (caller: ReturnType<typeof stubCaller>) =>
  caller.calls
    .filter((call) => call.method === "memory.list")
    .map((call) => call.params as MemoryListCommand)

const row = (description: string) => screen.findByRole("listitem", { name: description })

/** Unfold a node's row by its chevron, and look inside it. */
async function unfold(user: ReturnType<typeof userEvent.setup>, description: string) {
  const unfolded = await row(description)
  await user.click(within(unfolded).getByRole("button", { name: "Details" }))
  return within(unfolded)
}

const expanded = (unfolded: HTMLElement) =>
  within(unfolded).getByRole("button", { name: "Details" }).getAttribute("aria-expanded")

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
  file: PROFILE_FILE,
  actor: "app",
  thread_id: null,
  turn_id: null,
  diff: "",
  hash: "hash-1",
}

/** The page with its profile row unfolded. The profile was last changed by the user. */
async function openProfile(answers: Answers = {}) {
  const page = openPage(answers)
  await page.user.click(await screen.findByRole("button", { name: "Edit" }))
  return page
}

describe("the memory page", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/instances/instance-1/memory")
  })

  it("shows the profile, then the knowledge graph, as sections with no sub-tabs", async () => {
    openPage()

    expect(
      (await screen.findAllByRole("heading", { level: 2 })).map((heading) => heading.textContent),
    ).toEqual(["Profile", "Knowledge graph"])
    expect(screen.queryByRole("tab")).toBeNull()
  })

  it("lists the knowledge graph as rows, as the instance orders it", async () => {
    openPage()

    const nodes = await screen.findByRole("list", { name: "Facts and episodes" })
    expect(
      within(nodes)
        .getAllByRole("listitem")
        .map((item) => item.getAttribute("aria-label")),
    ).toEqual(["Fixed the deploy", "Likes coffee", "Picked markdown"])
    expect((await row("Likes coffee")).textContent).toContain("2026-09-02 · coffee · added by you")
  })

  it("unfolds a row under itself to its body, subjects and date, one row at a time", async () => {
    const { user } = openPage()

    const markdownRow = await unfold(user, "Picked markdown")

    expect(await markdownRow.findByText("The body of Picked markdown.")).toBeDefined()
    expect(markdownRow.getByRole("button", { name: "memory" })).toBeDefined()
    expect(expanded(await row("Picked markdown"))).toBe("true")
    await unfold(user, "Likes coffee")
    expect(expanded(await row("Picked markdown"))).toBe("false")
    expect(screen.queryByText("The body of Picked markdown.")).toBeNull()
  })

  it("unfolds the row of the node a link names", async () => {
    window.history.replaceState(null, "", `/instances/instance-1/memory/${deploy.node}`)
    const { caller } = openPage()

    const deployRow = await row("Fixed the deploy")
    expect(await within(deployRow).findByText("The body of Fixed the deploy.")).toBeDefined()
    expect(expanded(deployRow)).toBe("true")
    expect(called(caller, "memory.open")).toEqual([{ node: deploy.node }])
  })

  it("unfolds a linked node the list doesn't hold first, above the list", async () => {
    window.history.replaceState(null, "", `/instances/instance-1/memory/${deploy.node}`)
    openPage({ "memory.list": () => listing([coffee, markdown]) })

    const deployRow = await row("Fixed the deploy")
    expect(await within(deployRow).findByText("The body of Fixed the deploy.")).toBeDefined()
    const nodes = screen.getByRole("list", { name: "Facts and episodes" })
    expect(within(nodes).getAllByRole("listitem").at(0)).toBe(deployRow)
  })

  it("asks again for the unfolded node once a second under StrictMode, which remounts the page", async () => {
    window.history.replaceState(null, "", `/instances/instance-1/memory/${deploy.node}`)
    const clock = fakeClock()
    const caller = stubCaller({
      "memory.list": () => listing([deploy]),
      "memory.open": () => {
        throw new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })
      },
      "profile.get": () => profile,
      "config.history": () => ({ changes: [] }),
    })
    render(
      <StrictMode>
        <MemoryPage client={caller} clock={clock} instanceId="instance-1" />
      </StrictMode>,
    )
    await act(() => clock.advance(0))
    const opens = called(caller, "memory.open").length

    await act(() => clock.advance(1_000))

    expect(called(caller, "memory.open").length - opens).toBe(1)
  })

  it("says why a linked node forgotten since the link was made did not open", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/memory/2026-09-01-forgotten")
    openPage({
      "memory.open": () => {
        throw refused("NOT_FOUND")
      },
    })

    const forgotten = await row("2026-09-01-forgotten")
    expect(await within(forgotten).findByText("Refused with NOT_FOUND.")).toBeDefined()
  })

  it.each([
    [
      "another row unfolds",
      (user: ReturnType<typeof userEvent.setup>) => unfold(user, "Likes coffee"),
    ],
    [
      "its row folds",
      (user: ReturnType<typeof userEvent.setup>) => unfold(user, "Fixed the deploy"),
    ],
  ])("drops the linked node from the URL when %s", async (_, control) => {
    window.history.replaceState(null, "", `/instances/instance-1/memory/${deploy.node}`)
    const { user } = openPage()
    await screen.findByText("The body of Fixed the deploy.")
    const entries = window.history.length

    await control(user)

    expect(window.location.pathname).toBe("/instances/instance-1/memory")
    expect(window.history.length).toBe(entries)
  })

  it("narrows the list by search, kind, and date bounds", async () => {
    const { caller, user } = openPage()
    await row("Picked markdown")

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

  it("narrows the list to a subject clicked in an unfolded row, until the filter is cleared", async () => {
    const { caller, user } = openPage()
    const deployRow = await unfold(user, "Fixed the deploy")

    await user.click(await deployRow.findByRole("button", { name: "deploy" }))

    expect(listCalls(caller).at(-1)).toEqual({ query: "", subject: "deploy" })
    await user.click(screen.getByRole("button", { name: "Stop filtering by deploy" }))
    expect(listCalls(caller).at(-1)).toEqual({ query: "" })
  })

  it("loads the next page below the last row shown, and stops on the last page", async () => {
    const { caller, user } = openPage({
      "memory.list": ({ cursor }) =>
        cursor === undefined || cursor === null
          ? listing([deploy, coffee], coffee.node)
          : listing([markdown]),
    })

    await user.click(await screen.findByRole("button", { name: "Load more" }))

    expect(await row("Picked markdown")).toBeDefined()
    expect(await row("Fixed the deploy")).toBeDefined()
    expect(listCalls(caller).at(-1)).toEqual({ query: "", cursor: coffee.node })
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull()
  })

  it("says what recapped an episode and shows its tool path, linking to its thread", async () => {
    const { user } = openPage()

    const deployRow = await unfold(user, "Fixed the deploy")

    expect(await deployRow.findByText("Tool path: grep → bash")).toBeDefined()
    expect(deployRow.getByText(/Recap of a turn in/)).toBeDefined()
    await user.click(await deployRow.findByRole("button", { name: "Deploy day" }))
    expect(window.location.pathname).toBe(`/instances/instance-1/threads/${THREAD}`)
  })

  it("links to an archived thread, which only the full thread list has", async () => {
    const archived = { ...thread, title: "Old deploy day", archived: true }
    const { user } = openPage({
      "thread.list": ({ filter }) => ({
        threads: filter === "all" ? [archived] : [],
        ceiling: "full-access",
        cursor: null,
      }),
    })

    const deployRow = await unfold(user, "Fixed the deploy")
    await user.click(await deployRow.findByRole("button", { name: "Old deploy day" }))

    expect(window.location.pathname).toBe(`/instances/instance-1/threads/${THREAD}`)
  })

  it("says a factory run's episode recaps the run, with no thread", async () => {
    const shipped = summary({
      node: "2026-10-17-shipped-run-recaps",
      description: "Shipped run recaps",
      kind: "episode",
      source: "recap",
    })
    const { user } = openPage({
      "memory.list": () => listing([shipped]),
      "memory.open": () =>
        opened(shipped, {
          thread: null,
          run: "33333333-3333-3333-3333-333333333333",
          tools: [],
        }),
    })

    const shippedRow = await unfold(user, "Shipped run recaps")

    expect(await shippedRow.findByText("Recap of a factory run")).toBeDefined()
    expect(shippedRow.queryByText(/Added by you|Recap of a turn in|Tool path/)).toBeNull()
  })

  it("says a fact the agent remembered was remembered in its thread", async () => {
    const { user } = openPage()

    const markdownRow = await unfold(user, "Picked markdown")

    expect(await markdownRow.findByText(/Remembered in/)).toBeDefined()
    expect(await markdownRow.findByRole("button", { name: "Deploy day" })).toBeDefined()
    expect(markdownRow.queryByText(/Tool path/)).toBeNull()
  })

  it("says a fact the user added was added by them, with no thread", async () => {
    const { user } = openPage()

    const coffeeRow = await unfold(user, "Likes coffee")

    expect(await coffeeRow.findByText("Added by you")).toBeDefined()
    expect(coffeeRow.queryByText(/Remembered in|Recap of a turn in/)).toBeNull()
  })

  it("reads the list and the unfolded node again on Refresh, and never on its own", async () => {
    const clock = fakeClock()
    const caller = stubCaller({
      "memory.list": () => listing([markdown]),
      "memory.open": () => OPENED[markdown.node] as MemoryOpenResult,
      "thread.list": () => ({ threads: [thread], ceiling: "full-access", cursor: null }),
      "profile.get": () => profile,
      "config.history": () => ({ changes: [] }),
    })
    render(<MemoryPage client={caller} clock={clock} instanceId="instance-1" />)
    const user = userEvent.setup()
    const markdownRow = await unfold(user, "Picked markdown")
    await markdownRow.findByText("The body of Picked markdown.")
    const reads = () => caller.calls.filter((call) => call.method.startsWith("memory.")).length

    await act(() => clock.advance(10 * 60_000))
    expect(reads()).toBe(2)
    await user.click(screen.getByRole("button", { name: "Refresh" }))

    await markdownRow.findByText("The body of Picked markdown.")
    expect(caller.calls.filter((call) => call.method === "memory.list")).toHaveLength(2)
    expect(caller.calls.filter((call) => call.method === "memory.open")).toHaveLength(2)
  })

  it("shows the profile as one row with its file and an approximate token count", async () => {
    openPage()

    const section = await screen.findByRole("region", { name: "Profile" })
    expect(await within(section).findByText(PROFILE_FILE)).toBeDefined()
    expect(within(section).getByText("About 3 tokens in every prompt")).toBeDefined()
    expect(screen.queryByRole("textbox", { name: PROFILE_FILE })).toBeNull()
  })

  it("unfolds the profile to its editor, focused, saves it and says who changed it last", async () => {
    const { caller, user } = await openProfile({
      "profile.set": ({ text }) => ({ text, hash: "hash-2", tokens: 7 }),
    })

    const editor = await screen.findByRole("textbox", { name: PROFILE_FILE })
    expect(document.activeElement).toBe(editor)
    expect(editor).toHaveProperty("value", "Call me Jo.")
    expect(screen.getByText("Last changed by you, Sep 28, 2026, 10:04 AM")).toBeDefined()
    await user.type(editor, " Mornings only.")
    expect(screen.getByText("About 7 tokens in every prompt")).toBeDefined()
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Saved. It applies at the next turn.")).toBeDefined()
    expect(called(caller, "profile.set")).toEqual([
      { text: "Call me Jo. Mornings only.", hash: "hash-1" },
    ])
    expect(called(caller, "config.history")).toEqual([
      { file: PROFILE_FILE, limit: 1 },
      { file: PROFILE_FILE, limit: 1 },
    ])
    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true)
  })

  it("says the profile changed outside kinby after a hand edit, until the next save", async () => {
    let changes = [byYou]
    const { user } = await openProfile({
      "profile.get": () => ({ text: "Call me Jorge.", hash: "hash-3", tokens: 4 }),
      "profile.set": ({ text }) => {
        changes = [{ ...byYou, at: new Date(2026, 8, 28, 11, 30).toISOString(), hash: "hash-4" }]
        return { text, hash: "hash-4", tokens: 5 }
      },
      "config.history": () => ({ changes }),
    })

    const editor = await screen.findByRole("textbox", { name: PROFILE_FILE })
    expect(screen.getByText("Changed outside kinby")).toBeDefined()
    expect(screen.queryByText(/Last changed by/)).toBeNull()
    await user.type(editor, " Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Last changed by you, Sep 28, 2026, 11:30 AM")).toBeDefined()
    expect(screen.queryByText("Changed outside kinby")).toBeNull()
  })

  it("counts the profile's tokens as the instance does, by characters", async () => {
    const { user } = await openProfile({
      "profile.get": () => ({ text: "", hash: EMPTY, tokens: 0 }),
    })

    const editor = await screen.findByRole("textbox", { name: PROFILE_FILE })
    expect(screen.getByText("About 0 tokens in every prompt")).toBeDefined()
    await user.type(editor, "🌱🌱🌱🌱🌱")

    expect(screen.getByText("About 2 tokens in every prompt")).toBeDefined()
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

    const editor = await screen.findByRole("textbox", { name: PROFILE_FILE })
    await user.type(editor, " Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

    expect(await screen.findByDisplayValue("Call me Jorge.")).toBe(editor)
    expect(screen.getByText("About 4 tokens in every prompt")).toBeDefined()
    expect(screen.queryByRole("alert")).toBeNull()
    await user.type(editor, " Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(called(caller, "profile.set").at(-1)).toEqual({
      text: "Call me Jorge. Mine.",
      hash: "hash-3",
    })
  })

  it("opens Add fact at the top of the list, focused on its first field", async () => {
    const { user } = openWritablePage()
    const nodes = await screen.findByRole("list", { name: "Facts and episodes" })

    await user.click(screen.getByRole("button", { name: "Add fact" }))

    const form = screen.getByRole("form", { name: "Add fact" })
    expect(form.compareDocumentPosition(nodes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(document.activeElement).toBe(within(form).getByLabelText("Description"))
    expect(screen.getAllByRole("button", { name: "Add fact" }).at(0)).toHaveProperty(
      "disabled",
      true,
    )
  })

  it("adds a fact, then reloads the list and unfolds its row", async () => {
    const { caller, user } = openWritablePage()
    await row("Picked markdown")
    await user.click(screen.getByRole("button", { name: "Add fact" }))
    const form = within(screen.getByRole("form", { name: "Add fact" }))

    await user.type(form.getByLabelText("Description"), "Likes green tea")
    await user.type(form.getByLabelText("Subjects"), "drinks, mornings,")
    await user.type(form.getByLabelText("Body"), "No milk.")
    await user.click(form.getByRole("button", { name: "Add fact" }))

    expect(called(caller, "memory.add")).toEqual([
      { description: "Likes green tea", subjects: ["drinks", "mornings"], body: "No milk." },
    ])
    const teaRow = await row("Likes green tea")
    expect(await within(teaRow).findByText("The body of Likes green tea.")).toBeDefined()
    expect(expanded(teaRow)).toBe("true")
    expect(screen.queryByRole("form", { name: "Add fact" })).toBeNull()
    expect(listCalls(caller)).toHaveLength(2)
  })

  it("drops a new fact on Cancel", async () => {
    const { caller, user } = openWritablePage()
    await row("Picked markdown")
    await user.click(screen.getByRole("button", { name: "Add fact" }))

    await user.click(
      within(screen.getByRole("form", { name: "Add fact" })).getByRole("button", {
        name: "Cancel",
      }),
    )

    expect(screen.queryByRole("form", { name: "Add fact" })).toBeNull()
    expect(called(caller, "memory.add")).toEqual([])
  })

  it("corrects an unfolded fact from a copy of its fields and unfolds the new fact", async () => {
    const { caller, user } = openWritablePage()
    const markdownRow = await unfold(user, "Picked markdown")

    await user.click(await markdownRow.findByRole("button", { name: "Correct" }))
    const description = markdownRow.getByLabelText("Description")
    expect(document.activeElement).toBe(description)
    expect(description).toHaveProperty("value", "Picked markdown")
    expect(markdownRow.getByLabelText("Subjects")).toHaveProperty("value", "memory, kinby")
    await user.clear(description)
    await user.type(description, "Likes green tea")
    await user.click(markdownRow.getByRole("button", { name: "Save correction" }))

    expect(called(caller, "memory.correct")).toEqual([
      {
        node: markdown.node,
        description: "Likes green tea",
        subjects: ["memory", "kinby"],
        body: "The body of Picked markdown.",
      },
    ])
    const teaRow = await row("Likes green tea")
    expect(await within(teaRow).findByText("The body of Likes green tea.")).toBeDefined()
    expect(listCalls(caller)).toHaveLength(2)
  })

  it("drops a correction on Cancel and shows the fact again", async () => {
    const { caller, user } = openWritablePage()
    const markdownRow = await unfold(user, "Picked markdown")

    await user.click(await markdownRow.findByRole("button", { name: "Correct" }))
    await user.click(markdownRow.getByRole("button", { name: "Cancel" }))

    expect(markdownRow.getByText("The body of Picked markdown.")).toBeDefined()
    expect(called(caller, "memory.correct")).toEqual([])
  })

  it("offers only Forget on an unfolded episode", async () => {
    const { user } = openWritablePage()

    const deployRow = await unfold(user, "Fixed the deploy")

    expect(await deployRow.findByRole("button", { name: "Forget" })).toBeDefined()
    expect(deployRow.queryByRole("button", { name: "Correct" })).toBeNull()
  })

  it("forgets only after a confirmation that says it can't be undone, then folds the row", async () => {
    const { caller, user } = openWritablePage()
    const deployRow = await unfold(user, "Fixed the deploy")

    await user.click(await deployRow.findByRole("button", { name: "Forget" }))
    let dialog = await screen.findByRole("alertdialog", { name: "Forget this episode?" })
    expect(dialog.textContent).toContain("can't be undone")
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    expect(called(caller, "memory.forget")).toEqual([])
    await user.click(deployRow.getByRole("button", { name: "Forget" }))
    dialog = await screen.findByRole("alertdialog", { name: "Forget this episode?" })
    await user.click(within(dialog).getByRole("button", { name: "Forget" }))

    expect(called(caller, "memory.forget")).toEqual([{ node: deploy.node }])
    expect(expanded(await row("Fixed the deploy"))).toBe("false")
    expect(listCalls(caller)).toHaveLength(2)
  })

  it.each([
    [
      "a correction",
      async (user: ReturnType<typeof userEvent.setup>, unfolded: ReturnType<typeof within>) => {
        await user.click(await unfolded.findByRole("button", { name: "Correct" }))
        await user.click(unfolded.getByRole("button", { name: "Save correction" }))
      },
    ],
    [
      "a forget",
      async (user: ReturnType<typeof userEvent.setup>, unfolded: ReturnType<typeof within>) => {
        await user.click(await unfolded.findByRole("button", { name: "Forget" }))
        const dialog = await screen.findByRole("alertdialog", { name: "Forget this fact?" })
        await user.click(within(dialog).getByRole("button", { name: "Forget" }))
      },
    ],
  ])("says the node is gone and reloads the list when %s finds it gone", async (_, write) => {
    const gone = () => {
      throw refused("NOT_FOUND")
    }
    const { caller, user } = openWritablePage({ "memory.correct": gone, "memory.forget": gone })
    const markdownRow = await unfold(user, "Picked markdown")

    await write(user, markdownRow)

    expect(await screen.findByText("That node is gone")).toBeDefined()
    expect(expanded(await row("Picked markdown"))).toBe("false")
    expect(listCalls(caller)).toHaveLength(2)
  })

  it("shows each field's error beside the field the instance refused", async () => {
    const { user } = openWritablePage({
      "memory.add": () => {
        throw refused("INVALID_ARGUMENT", { description: "Describe the fact." })
      },
    })
    await row("Picked markdown")
    await user.click(screen.getByRole("button", { name: "Add fact" }))
    const form = within(screen.getByRole("form", { name: "Add fact" }))

    await user.type(form.getByLabelText("Description"), " ")
    await user.click(form.getByRole("button", { name: "Add fact" }))

    expect(await form.findByText("Describe the fact.")).toBeDefined()
    expect(form.getByLabelText("Description").getAttribute("aria-invalid")).toBe("true")
    expect(form.getByLabelText("Subjects").getAttribute("aria-invalid")).toBeNull()
  })
})
