import { instanceSummary, stubCaller } from "@kinby/contract/testing"
import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { listed, openLibrary } from "@/components/library-testing"

function openPage() {
  const caller = stubCaller({
    "library.list": () => listed,
    "factory.get": ({ name }) => ({
      name,
      source: "shipped",
      files: { "factory.yaml": `name: ${name}\n`, "prompts/implement.md": "Implement it.\n" },
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
    "factory.run.list": () => ({ runs: [] }),
  })
  return openLibrary(caller, "/library/factories")
}

describe("LibraryPage, a factory", () => {
  it("unfolds a factory to read its factory file", async () => {
    const user = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const file = await screen.findByRole("textbox", { name: "factory.yaml" })
    expect((file as HTMLTextAreaElement).value).toBe("name: software\n")
    expect(file.hasAttribute("readonly")).toBe(true)
    expect(screen.getByText("Other files: prompts/implement.md")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull()
  })

  it("unfolds a factory to list the instances that hold its roles", async () => {
    const user = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    expect(await screen.findByRole("region", { name: "software-coder" })).toBeDefined()
  })
})
