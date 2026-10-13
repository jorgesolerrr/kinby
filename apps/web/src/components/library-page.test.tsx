import { CallError } from "@kinby/contract"
import type { LibraryEntry, LibraryGitPreviewResult, LibraryListResult } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { LibraryPage } from "@/components/library-page"

const listed: LibraryListResult = {
  entries: [
    { kind: "factory", name: "software", description: null, source: "shipped" },
    { kind: "factory", name: "tickets", description: null, source: "written" },
    { kind: "skill", name: "tdd", description: "Test first.", source: "shipped" },
    { kind: "skill", name: "unslop", description: "Cut AI tells.", source: "shipped" },
  ],
}

const tdd = "---\nname: tdd\ndescription: Test first.\n---\nRed, green, refactor.\n"

function openPage() {
  const caller = stubCaller({
    "library.list": () => listed,
    "library.skill.read": ({ name }) => ({
      content: name === "tdd" ? tdd : "",
      files: ["CREDITS.md", "LICENSE", "tests.md"],
      hash: "h-1",
    }),
    "factory.get": ({ name }) => ({
      name,
      source: "shipped",
      files: { "factory.yaml": `name: ${name}\n`, "prompts/implement.md": "Implement it.\n" },
      hash: "h-2",
    }),
  })
  render(<LibraryPage client={caller} clock={fakeClock()} />)
  return { caller, user: userEvent.setup() }
}

const names = (list: HTMLElement) =>
  within(list)
    .getAllByRole("button")
    .map((item) => item.getAttribute("aria-label"))

describe("LibraryPage", () => {
  it("lists the skills and the factories apart, each with where it came from", async () => {
    openPage()

    const skills = await screen.findByRole("list", { name: "Skills" })
    const factories = screen.getByRole("list", { name: "Factories" })
    expect(names(skills)).toEqual(["tdd, shipped skill", "unslop, shipped skill"])
    expect(names(factories)).toEqual(["software, shipped factory", "tickets, written factory"])
    expect(within(skills).getByText("Test first.")).toBeTruthy()
  })

  it("opens a skill to read its SKILL.md and lists its other files", async () => {
    const { caller, user } = openPage()

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))

    const body = await screen.findByRole("textbox", { name: "SKILL.md" })
    expect((body as HTMLTextAreaElement).value).toBe(tdd)
    expect(body.hasAttribute("readonly")).toBe(true)
    expect(screen.getByText("Other files: CREDITS.md, LICENSE, tests.md")).toBeTruthy()
    expect(caller.calls.filter((call) => call.method === "library.skill.read")).toEqual([
      { method: "library.skill.read", params: { name: "tdd" } },
    ])
  })

  it("opens a factory to read its factory file", async () => {
    const { user } = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const file = await screen.findByRole("textbox", { name: "factory.yaml" })
    expect((file as HTMLTextAreaElement).value).toBe("name: software\n")
    expect(screen.getByText("Other files: prompts/implement.md")).toBeTruthy()
  })
})

const commit = "0123456789abcdef0123456789abcdef01234567"
const grill = "---\nname: grill\ndescription: Ask why.\n---\nAsk why five times.\n"

const shown: LibraryGitPreviewResult = {
  url: "https://github.com/owner/repo.git",
  ref: "main",
  commit,
  skills: [
    { name: "caveman", path: "skills/caveman", content: "Talk short.\n", files: [] },
    { name: "grill", path: "skills/grill", content: grill, files: ["references/questions.md"] },
  ],
}

const added: LibraryEntry = {
  kind: "skill",
  name: "grill",
  description: "Ask why.",
  source: "git",
  git: { url: shown.url, path: "skills/grill", ref: "main", commit },
}

function openGitPage(preview: () => LibraryGitPreviewResult) {
  let entries = listed.entries
  const caller = stubCaller({
    "library.list": () => ({ entries }),
    "library.git.preview": preview,
    "library.git.add": () => {
      entries = [...entries, added]
      return { entries: [added] }
    },
  })
  render(<LibraryPage client={caller} clock={fakeClock()} />)
  return { caller, user: userEvent.setup() }
}

describe("LibraryPage, adding from git", () => {
  it("walks the address, the preview and the confirm, then lists the entry with its commit", async () => {
    const { caller, user } = openGitPage(() => shown)

    await user.click(await screen.findByRole("button", { name: "Add from git" }))
    await user.type(screen.getByRole("textbox", { name: "Address" }), "owner/repo#main")
    await user.click(screen.getByRole("button", { name: "Preview" }))

    expect(await screen.findByText(`At commit ${commit}`)).toBeTruthy()
    const body = screen.getByRole("textbox", { name: "grill SKILL.md" })
    expect((body as HTMLTextAreaElement).value).toBe(grill)
    expect(screen.getByText("Other files: references/questions.md")).toBeTruthy()
    expect(caller.calls.some((call) => call.method === "library.git.add")).toBe(false)

    await user.click(screen.getByRole("checkbox", { name: "caveman" }))
    await user.click(screen.getByRole("button", { name: "Add 1 skill" }))

    expect(await screen.findByRole("button", { name: "grill, git skill" })).toBeTruthy()
    expect(screen.getByText(`${shown.url} at ${commit}`)).toBeTruthy()
    expect(caller.calls.filter((call) => call.method.startsWith("library.git"))).toEqual([
      { method: "library.git.preview", params: { address: "owner/repo#main" } },
      {
        method: "library.git.add",
        params: { address: "owner/repo#main", commit, skills: ["grill"] },
      },
    ])
    expect(screen.queryByRole("textbox", { name: "Address" })).toBeNull()
  })

  it("shows why the hub refused an address", async () => {
    const { user } = openGitPage(() => {
      throw new CallError({
        code: "INVALID_ARGUMENT",
        message: "Some values are invalid.",
        retryable: false,
        fields: { address: "skills/grill/logo.png is a binary file. A skill holds only text." },
      })
    })

    await user.click(await screen.findByRole("button", { name: "Add from git" }))
    await user.type(screen.getByRole("textbox", { name: "Address" }), "owner/repo")
    await user.click(screen.getByRole("button", { name: "Preview" }))

    const alert = await screen.findByRole("alert")
    expect(within(alert).getByText(/logo.png is a binary file/)).toBeTruthy()
  })
})
