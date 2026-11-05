import { CallError } from "@kinby/contract"
import type { InstancePicksResult, LibraryEntry } from "@kinby/contract"
import {
  type Answers,
  fakeClock,
  instanceSummary,
  type StubCaller,
  stubCaller,
} from "@kinby/contract/testing"
import { act, screen, waitFor, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { commit, listed, names, openLibrary, tdd } from "@/components/library-testing"

const yours: LibraryEntry[] = [
  { kind: "skill", name: "grill", description: "Ask why.", source: "written" },
  {
    kind: "skill",
    name: "pr",
    description: "Write the body.",
    source: "git",
    git: { url: "https://github.com/owner/repo.git", path: "skills/pr", ref: null, commit },
    edited: true,
  },
]

const servers: LibraryEntry[] = [
  { kind: "mcp_server", name: "github", description: "Issues and pulls.", source: "written" },
  { kind: "mcp_server", name: "linear", description: "Tickets first.", source: "written" },
]

const home = instanceSummary({ instance_id: "instance-home", persona_name: "Kin", home: true })

function openPage(entries: LibraryEntry[] = listed.entries, path?: string) {
  const caller = stubCaller({
    "library.list": () => ({ entries }),
    "library.skill.read": () => ({ content: tdd, files: [], hash: "h-1" }),
  })
  return { caller, user: openLibrary(caller, path, undefined, home) }
}

const header = () => within(screen.getByRole("heading", { name: "Library" }).closest("header")!)

const tab = (name: string) => screen.getByRole("tab", { name: new RegExp(`^${name}`) })

describe("LibraryPage", () => {
  it("lists the skills and the factories apart, each with where it came from", async () => {
    const { user } = openPage()

    const skills = await screen.findByRole("list", { name: "Shipped with kinby" })
    expect(names(skills)).toEqual(["tdd, shipped skill", "unslop, shipped skill"])
    expect(within(skills).getByText("Test first.")).toBeTruthy()
    expect(screen.getByRole("tab", { name: "Skills 2" })).toBeTruthy()
    expect(screen.queryByText("Nothing open")).toBeNull()
    expect(screen.queryByRole("list", { name: "Yours" })).toBeNull()
    expect(screen.queryByText("No skills yet")).toBeNull()
    await user.click(screen.getByRole("tab", { name: "Factories 2" }))

    const factories = await screen.findByRole("list", { name: "Factories" })
    expect(names(factories)).toEqual(["software, shipped factory", "tickets, written factory"])
    expect(screen.queryByRole("list", { name: "Shipped with kinby" })).toBeNull()
  })

  it("splits the skills into yours and those shipped with kinby", async () => {
    openPage([...listed.entries, ...yours])

    const mine = await screen.findByRole("list", { name: "Yours" })
    expect(names(mine)).toEqual(["grill, written skill", "pr, git skill, edited"])
    const pr = within(mine).getByRole("button", { name: "pr, git skill, edited" })
    expect(within(pr).getByText("Edited")).toBeTruthy()
    expect(within(pr).getByText("Git")).toBeTruthy()
    expect(names(screen.getByRole("list", { name: "Shipped with kinby" }))).toEqual([
      "tdd, shipped skill",
      "unslop, shipped skill",
    ])
  })

  it("searches the names and descriptions", async () => {
    const { user } = openPage()
    await screen.findByRole("list", { name: "Shipped with kinby" })

    await user.type(screen.getByRole("searchbox", { name: "Search the library" }), "ai tells")

    expect(names(screen.getByRole("list", { name: "Shipped with kinby" }))).toEqual([
      "unslop, shipped skill",
    ])
    await user.type(screen.getByRole("searchbox", { name: "Search the library" }), " and more")
    expect(screen.getByText('Nothing here matches "ai tells and more"')).toBeTruthy()
    expect(screen.queryByRole("button", { name: /^\d+ in / })).toBeNull()
    expect(screen.queryByText("No skills yet")).toBeNull()
    expect(screen.queryByRole("button", { name: "Write a skill" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Ask Kin to add one" })).toBeNull()
  })

  it("searches every kind at once, and each tab counts its matches", async () => {
    const { user } = openPage([...listed.entries, ...servers])
    await screen.findByRole("list", { name: "Shipped with kinby" })
    const search = screen.getByRole("searchbox", { name: "Search the library" })

    await user.type(search, "first")

    expect(tab("Skills").textContent).toBe("Skills 1")
    expect(tab("MCP servers").textContent).toBe("MCP servers 1")
    expect(tab("Factories").textContent).toBe("Factories 0")
    await user.clear(search)
    expect(tab("Skills").textContent).toBe("Skills 2")
    expect(tab("MCP servers").textContent).toBe("MCP servers 2")
    expect(tab("Factories").textContent).toBe("Factories 2")
  })

  it("links a tab with no matches to the tabs that have some, keeping the search", async () => {
    const { user } = openPage([...listed.entries, ...servers])
    await screen.findByRole("list", { name: "Shipped with kinby" })

    await user.type(screen.getByRole("searchbox", { name: "Search the library" }), "github")

    expect(screen.getByText('Nothing here matches "github"')).toBeTruthy()
    expect(screen.queryByRole("button", { name: /in Factories/ })).toBeNull()
    await user.click(screen.getByRole("button", { name: "1 in MCP servers" }))

    expect(window.location.pathname).toBe("/library/mcp-servers")
    expect(tab("MCP servers").getAttribute("aria-selected")).toBe("true")
    expect(screen.getByRole("searchbox", { name: "Search the library" })).toHaveProperty(
      "value",
      "github",
    )
    expect(names(await screen.findByRole("list", { name: "MCP servers" }))).toEqual([
      "github, written MCP server",
    ])
  })

  it("gives each tab its own address, and Back moves between them", async () => {
    const { user } = openPage()
    await screen.findByRole("list", { name: "Shipped with kinby" })

    await user.click(tab("MCP servers"))
    expect(window.location.pathname).toBe("/library/mcp-servers")
    await user.click(tab("Factories"))
    expect(window.location.pathname).toBe("/library/factories")
    expect(await screen.findByRole("list", { name: "Factories" })).toBeTruthy()

    act(() => window.history.back())

    await waitFor(() => expect(window.location.pathname).toBe("/library/mcp-servers"))
    expect(tab("MCP servers").getAttribute("aria-selected")).toBe("true")
  })

  it("opens on the tab its address names", async () => {
    openPage(listed.entries, "/library/factories")

    expect(await screen.findByRole("list", { name: "Factories" })).toBeTruthy()
    expect(tab("Factories").getAttribute("aria-selected")).toBe("true")
  })

  it("unfolds one row at a time, under itself", async () => {
    const { user } = openPage()

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))
    expect(await screen.findByRole("textbox", { name: "SKILL.md" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "tdd, shipped skill", expanded: true })).toBeTruthy()
    await user.click(screen.getByRole("button", { name: "unslop, shipped skill" }))

    expect(
      await screen.findByRole("button", { name: "unslop, shipped skill", expanded: true }),
    ).toBeTruthy()
    expect(screen.getByRole("button", { name: "tdd, shipped skill", expanded: false })).toBeTruthy()
    await waitFor(() =>
      expect(screen.getAllByRole("textbox", { name: "SKILL.md" })).toHaveLength(1),
    )
    const unslop = screen.getByRole("listitem", { name: "unslop" })
    expect(within(unslop).getByRole("textbox", { name: "SKILL.md" })).toBeTruthy()
  })

  it("sits each section's rows in one bordered group, and an unfolded row's detail in a panel", async () => {
    const { user } = openPage([...listed.entries, ...yours])

    for (const section of ["Yours", "Shipped with kinby"]) {
      const list = await screen.findByRole("list", { name: section })
      expect(list.getAttribute("data-variant")).toBe("outline")
      for (const row of within(list).getAllByRole("button")) {
        expect(row.getAttribute("data-size")).toBe("sm")
      }
    }
    await user.click(screen.getByRole("button", { name: "tdd, shipped skill" }))

    const editor = await screen.findByRole("textbox", { name: "SKILL.md" })
    expect(editor.closest("[data-slot=collapsible-content]")?.getAttribute("data-variant")).toBe(
      "muted",
    )
    await user.click(tab("Factories"))
    const factories = await screen.findByRole("list", { name: "Factories" })
    expect(factories.getAttribute("data-variant")).toBe("outline")
  })

  it.each([
    ["Write a skill", "Name"],
    ["Add from git", "Address"],
  ])("opens %s at the top of the list, on its first field", async (form, field) => {
    const { user } = openPage()
    const list = await screen.findByRole("list", { name: "Shipped with kinby" })

    await user.click(screen.getByRole("button", { name: "New skill" }))
    await user.click(await screen.findByRole("menuitem", { name: form }))

    const panel = await screen.findByRole("region", { name: form })
    expect(panel.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await waitFor(() =>
      expect(document.activeElement).toBe(within(panel).getByRole("textbox", { name: field })),
    )
  })

  it("opens Add MCP server at the top of the list, on its name", async () => {
    const { user } = openPage(listed.entries, "/library/mcp-servers")
    await screen.findByText("No MCP servers yet")
    expect(screen.queryByRole("button", { name: /^\d+ in / })).toBeNull()

    await user.click(header().getByRole("button", { name: "Add MCP server" }))

    const panel = await screen.findByRole("region", { name: "Add an MCP server" })
    expect(document.activeElement).toBe(within(panel).getByRole("textbox", { name: "Name" }))
  })

  it.each([
    [
      "/library",
      "No skills yet",
      "A skill teaches an instance one kind of task. Write one, or add some from a git repository.",
      "Write a skill",
      "Write a skill",
    ],
    [
      "/library/mcp-servers",
      "No MCP servers yet",
      "An MCP server gives an instance tools from outside kinby, like GitHub or Notion. Add one here, then pick it for an instance.",
      "Add MCP server",
      "Add an MCP server",
    ],
  ])(
    "shows an empty %s as the empty block, whose add action opens the form",
    async (path, title, lead, add, form) => {
      const { user } = openPage([], path)

      const block = within(
        (await screen.findByText(title)).closest<HTMLElement>("[data-slot=empty]")!,
      )
      expect(block.getByText(lead)).toBeTruthy()
      await user.click(block.getByRole("button", { name: add }))

      expect(await screen.findByRole("region", { name: form })).toBeTruthy()
    },
  )

  it("asks the home instance to add one, on the home screen sent to it", async () => {
    const { user } = openPage([], "/library/mcp-servers")

    await user.click(await screen.findByRole("button", { name: "Ask Kin to add one" }))

    expect(window.location.pathname).toBe("/")
    expect(window.location.search).toBe("?to=instance-home")
  })

  it("shows an empty Factories tab with no add action, naming the write-factory skill", async () => {
    openPage([], "/library/factories")

    expect(await screen.findByText("No factories yet")).toBeTruthy()
    expect(
      screen.getByText(
        "A factory runs work through steps across instances. Ask an instance with the write-factory skill to make one.",
      ),
    ).toBeTruthy()
    expect(screen.queryByRole("button", { name: /^(Add|Write)/ })).toBeNull()
    expect(screen.getByRole("button", { name: "Ask Kin to add one" })).toBeTruthy()
  })

  it("points to the write-factory skill for a new factory", async () => {
    openPage(listed.entries, "/library/factories")

    expect(await screen.findByText(/with the write-factory skill/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: "New skill" })).toBeNull()
  })
})

describe("Used by", () => {
  const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada" })
  const coder = instanceSummary({ instance_id: "instance-2", persona_name: "Coder" })
  const broken = instanceSummary({ instance_id: "instance-3", persona_name: "Broken" })
  const picks: Record<string, InstancePicksResult> = {
    "instance-1": {
      hash: "p-1",
      picks: [{ name: "tdd", source: "shipped", state: "up-to-date" }],
      mcp_servers: [{ name: "github", state: "edited", trust_hints: false }],
    },
    "instance-2": {
      hash: "p-2",
      picks: [{ name: "tdd", source: "shipped", state: "behind" }],
      mcp_servers: [],
    },
  }

  function openUsed(path?: string, answers: Answers = {}, clock = fakeClock()) {
    const caller = stubCaller({
      "library.list": () => ({ entries: [...listed.entries, ...servers] }),
      "library.skill.read": () => ({ content: tdd, files: [], hash: "h-1" }),
      "library.mcp_server.read": ({ name }) => ({
        name,
        server: { transport: { type: "url", url: "https://example.com/mcp" } },
        hash: "m-1",
      }),
      "instance.list": () => ({ instances: [ada, coder, broken] }),
      "instance.picks.get": ({ instance_id }) => {
        const read = picks[instance_id]
        if (read === undefined) throw new Error("The instance's files are unreadable.")
        return read
      },
      ...answers,
    })
    return { caller, user: openLibrary(caller, path, clock) }
  }

  const picksRead = (caller: StubCaller) =>
    caller.calls.filter((call) => call.method === "instance.picks.get").length

  it("shows on each row the avatars of the instances that picked it, or Not picked", async () => {
    openUsed()

    const row = await screen.findByRole("button", { name: "tdd, shipped skill" })
    await waitFor(() =>
      expect(
        within(row)
          .getAllByRole("img")
          .map((avatar) => avatar.ariaLabel),
      ).toEqual(["Ada", "Coder"]),
    )
    const unslop = screen.getByRole("button", { name: "unslop, shipped skill" })
    expect(within(unslop).getByText("Not picked")).toBeTruthy()
    expect(within(unslop).queryByRole("img")).toBeNull()
  })

  it("lists each instance that picked a skill with how its copy stands, leaving out one that fails", async () => {
    const { user } = openUsed()

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))

    const usedBy = await screen.findByRole("list", { name: "Used by" })
    const instances = within(usedBy).getAllByRole("listitem")
    expect(instances.map((each) => each.getAttribute("aria-label"))).toEqual(["Ada", "Coder"])
    expect(within(instances[0]).getByText("Up to date")).toBeTruthy()
    expect(within(instances[1]).getByText("Behind the library")).toBeTruthy()
  })

  it("lists each instance that picked an MCP server, and Open goes to its Config", async () => {
    const { user } = openUsed("/library/mcp-servers")

    await user.click(await screen.findByRole("button", { name: "github, written MCP server" }))

    const ada = within(await screen.findByRole("list", { name: "Used by" })).getByRole("listitem", {
      name: "Ada",
    })
    expect(within(ada).getByText("Edited in the instance")).toBeTruthy()
    await user.click(within(ada).getByRole("button", { name: "Open" }))
    expect(window.location.pathname).toBe("/instances/instance-1/config")
  })

  it("reads every instance's picks again after the connection drops mid-read", async () => {
    const clock = fakeClock()
    let dropped = false
    openUsed(
      undefined,
      {
        "instance.picks.get": ({ instance_id }) => {
          if (instance_id === "instance-2" && !dropped) {
            dropped = true
            throw new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })
          }
          const read = picks[instance_id]
          if (read === undefined) throw new Error("The instance's files are unreadable.")
          return read
        },
      },
      clock,
    )
    const row = await screen.findByRole("button", { name: "tdd, shipped skill" })

    await act(() => clock.advance(1_000))

    await waitFor(() =>
      expect(
        within(row)
          .getAllByRole("img")
          .map((avatar) => avatar.ariaLabel),
      ).toEqual(["Ada", "Coder"]),
    )
  })

  it("says nobody picked an entry, and where to pick it", async () => {
    const { user } = openUsed()

    await user.click(await screen.findByRole("button", { name: "unslop, shipped skill" }))

    expect(
      await screen.findByText("No instance picked unslop. Pick it in an instance's Config."),
    ).toBeTruthy()
    expect(screen.queryByRole("list", { name: "Used by" })).toBeNull()
  })

  it.each([
    ["a save", "Save", undefined],
    ["a delete", "Delete", "Delete"],
  ])("reads again after %s", async (_, action, confirm) => {
    const { caller, user } = openUsed(undefined, {
      "library.skill.write": () => ({ content: tdd, files: [], hash: "h-2" }),
      "library.skill.delete": () => ({ entries: [] }),
    })
    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))
    const editor = await screen.findByRole("textbox", { name: "SKILL.md" })
    await screen.findByRole("list", { name: "Used by" })
    const before = picksRead(caller)
    await user.type(editor, "More.")

    await user.click(screen.getByRole("button", { name: action }))
    if (confirm !== undefined) {
      const dialog = await screen.findByRole("alertdialog")
      await user.click(within(dialog).getByRole("button", { name: confirm }))
    }

    await waitFor(() => expect(picksRead(caller)).toBe(before + 3))
  })

  it("reads again after a move", async () => {
    const { caller, user } = openUsed(undefined, {
      "library.list": () => ({ entries: [{ ...listed.entries[2], newer: true }] }),
      "library.skill.check": () => ({ newer: { commit: null, diff: "+More.", hash: "h-2" } }),
      "library.skill.move": () => ({ entries: [] }),
    })
    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill, newer" }))
    await screen.findByRole("list", { name: "Used by" })
    const before = picksRead(caller)
    await user.click(await screen.findByRole("button", { name: "Read the diff" }))

    await user.click(await screen.findByRole("button", { name: "Move to this version" }))

    await waitFor(() => expect(picksRead(caller)).toBe(before + 3))
  })
})
