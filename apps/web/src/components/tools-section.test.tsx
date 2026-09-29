import type { ToolListResult } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ToolsSection } from "@/components/tools-section"

const listed: ToolListResult = {
  tools: [
    { name: "deploy", source: "kinby-factory 0.3.0", write: true, rule: "deny" },
    { name: "skill", source: "core", write: false, rule: "mode" },
    { name: "skill_write", source: "core", write: true, rule: "ask" },
    { name: "weather", source: "tools/weather.py", write: false, rule: "allow" },
  ],
  warnings: [
    { type: "warning", sources: ["tools/broken.py"], message: "SyntaxError: invalid syntax" },
  ],
}

function openSection(onOpenPermissions?: () => void) {
  const caller = stubCaller({ "tool.list": () => listed })
  render(<ToolsSection client={caller} onOpenPermissions={onOpenPermissions} />)
  return userEvent.setup()
}

describe("ToolsSection", () => {
  it("lists each tool with where it comes from, whether it writes, and its rule", async () => {
    openSection()

    const table = await screen.findByRole("table", { name: "Tools" })
    const rows = within(table)
      .getAllByRole("row")
      .map((row) =>
        [...within(row).queryAllByRole("columnheader"), ...within(row).queryAllByRole("cell")].map(
          (cell) => cell.textContent,
        ),
      )
    expect(rows).toEqual([
      ["Tool", "Source", "Access", "Rule"],
      ["deploy", "kinby-factory 0.3.0", "Writes", "Deny"],
      ["skill", "core", "Reads", "Follows the mode"],
      ["skill_write", "core", "Writes", "Ask"],
      ["weather", "tools/weather.py", "Reads", "Allow"],
    ])
    expect(screen.getByText("tools/broken.py: SyntaxError: invalid syntax")).toBeDefined()
  })

  it("links to Permissions to change a rule", async () => {
    const open = vi.fn()
    const user = openSection(open)

    await user.click(await screen.findByRole("button", { name: "Change a rule in Permissions" }))

    expect(open).toHaveBeenCalledOnce()
  })

  it("disables the link while Permissions is not available", async () => {
    openSection()

    expect(
      await screen.findByRole("button", { name: "Change a rule in Permissions" }),
    ).toHaveProperty("disabled", true)
  })
})
