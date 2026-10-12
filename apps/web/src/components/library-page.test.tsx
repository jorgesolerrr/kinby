import type { LibraryListResult } from "@kinby/contract"
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
