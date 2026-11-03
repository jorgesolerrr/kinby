import type { LibraryEntry } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
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

function openPage(entries: LibraryEntry[] = listed.entries, path?: string) {
  const caller = stubCaller({
    "library.list": () => ({ entries }),
    "library.skill.read": () => ({ content: tdd, files: [], hash: "h-1" }),
  })
  return { caller, user: openLibrary(caller, path) }
}

const tab = (name: string) => screen.getByRole("tab", { name: new RegExp(`^${name}`) })

describe("LibraryPage", () => {
  it("lists the skills and the factories apart, each with where it came from", async () => {
    const { user } = openPage()

    const skills = await screen.findByRole("list", { name: "Shipped with kinby" })
    expect(names(skills)).toEqual(["tdd, shipped skill", "unslop, shipped skill"])
    expect(within(skills).getByText("Test first.")).toBeTruthy()
    expect(screen.getByRole("tab", { name: "Skills 2" })).toBeTruthy()
    expect(screen.queryByText("Nothing open")).toBeNull()
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
    expect(screen.getByText('Nothing matches "ai tells and more"')).toBeTruthy()
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

    await user.click(screen.getByRole("button", { name: "Add MCP server" }))

    const panel = await screen.findByRole("region", { name: "Add an MCP server" })
    expect(document.activeElement).toBe(within(panel).getByRole("textbox", { name: "Name" }))
  })

  it("points to the write-factory skill for a new factory", async () => {
    openPage(listed.entries, "/library/factories")

    expect(await screen.findByText(/with the write-factory skill/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: "New skill" })).toBeNull()
  })
})
