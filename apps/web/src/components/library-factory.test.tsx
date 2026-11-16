import { stubCaller } from "@kinby/contract/testing"
import { screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { listed, openLibrary } from "@/components/library-testing"

const factoryFile = `name: software
requires: { image: coder }
work_item: { issue: int }
steps:
  - { id: check, kind: code, call: tools/github.py:check }
  - { id: implement, kind: agent, engine: claude, prompt: prompts/implement.md }
  - { id: publish, kind: code, call: tools/github.py:publish }
`

function openPage(file = factoryFile) {
  const caller = stubCaller({
    "library.list": () => listed,
    "factory.get": ({ name }) => ({
      name,
      source: "shipped",
      files: { "factory.yaml": file, "prompts/implement.md": "Implement it.\n" },
      hash: "h-2",
    }),
  })
  return openLibrary(caller, "/library/factories")
}

const steps = () =>
  within(screen.getByRole("list", { name: "Steps" }))
    .getAllByRole("listitem")
    .map((step) => step.textContent)

describe("LibraryPage, a factory", () => {
  it("unfolds a factory to read its factory file", async () => {
    const user = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const file = await screen.findByRole("textbox", { name: "factory.yaml" })
    expect((file as HTMLTextAreaElement).value).toBe(factoryFile)
    expect(file.hasAttribute("readonly")).toBe(true)
    expect(screen.getByText("Other files: prompts/implement.md")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull()
  })

  it("unfolds a factory to show its steps", async () => {
    const user = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    await screen.findByRole("list", { name: "Steps" })
    expect(steps()).toEqual(["check", "implement", "publish"])
  })

  it("reads a step's merge keys as the hub does", async () => {
    const user = openPage(`name: software
requires: {}
work_item: { issue: int }
steps:
  - &check { id: check, kind: command, run: [pytest] }
  - { <<: *check, id: recheck }
`)

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    await screen.findByRole("list", { name: "Steps" })
    expect(steps()).toEqual(["check", "recheck"])
  })
})
