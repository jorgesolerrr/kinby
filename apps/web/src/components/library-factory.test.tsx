import { instanceSummary, stubCaller } from "@kinby/contract/testing"
import { screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { listed, openLibrary } from "@/components/library-testing"

const factoryFile = `name: software
instances:
  coder:
    image: coder
    suggested_picks: [tdd, unslop]
    setup_fields:
      - { name: repository, label: Repository, kind: config, type: url }
      - { name: GH_TOKEN, label: GitHub token, kind: secret, type: text }
  reviewer: {}
steps:
  - { id: prepare, kind: code }
  - { id: implement, kind: client }
  - { id: publish, kind: code }
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
    "instance.list": () => ({
      instances: [
        instanceSummary({
          instance_id: "instance-1",
          manifest_id: "software-coder",
          factory_role: { factory: "software", name: "coder" },
        }),
      ],
    }),
    "factory.run.list": () => ({ runs: [], cursor: null }),
  })
  return openLibrary(caller, "/library/factories")
}

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

  it("unfolds a factory to list the instances that hold its roles", async () => {
    const user = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    expect(await screen.findByRole("region", { name: "software-coder" })).toBeDefined()
  })

  it("unfolds a factory to show its roles and steps after its instances", async () => {
    const user = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const madeOf = await screen.findByRole("heading", { name: "What it's made of" })
    const instances = screen.getByRole("heading", { name: "Instances and runs" })
    expect(
      instances.compareDocumentPosition(madeOf) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    const coder = screen.getByRole("region", { name: "coder role" })
    expect(within(coder).getByText(/image coder/)).toBeDefined()
    expect(within(coder).getByText("tdd")).toBeDefined()
    expect(within(coder).getByText("unslop")).toBeDefined()
    expect(within(coder).getByText("Repository, GitHub token")).toBeDefined()
    const reviewer = screen.getByRole("region", { name: "reviewer role" })
    expect(within(reviewer).getByText(/kinby's base image/)).toBeDefined()
    expect(within(reviewer).getAllByText("None")).toHaveLength(2)
    const steps = screen.getByRole("list", { name: "Steps" })
    expect(
      within(steps)
        .getAllByRole("listitem")
        .map((step) => step.textContent),
    ).toEqual(["prepare", "implement", "publish"])
  })

  it("reads a role's merge keys as the hub does", async () => {
    const user = openPage(`name: software
instances:
  coder: &coder
    image: coder
    suggested_picks: [tdd]
  reviewer:
    <<: *coder
steps:
  - { id: prepare, kind: code }
`)

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const reviewer = await screen.findByRole("region", { name: "reviewer role" })
    expect(within(reviewer).getByText(/image coder/)).toBeDefined()
    expect(within(reviewer).getByText("tdd")).toBeDefined()
  })
})
