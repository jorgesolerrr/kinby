import { CallError } from "@kinby/contract"
import type { ConfigChange, PromptResult } from "@kinby/contract"
import { type Answers, stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ConfigPanel } from "@/components/config-panel"

const behavior: PromptResult = { content: "Be brief.\n", hash: "hash-1", default: false }

const byTheAgent: ConfigChange = {
  // Local time, so the page shows the same clock time wherever the test runs.
  at: new Date(2026, 8, 28, 10, 4).toISOString(),
  file: "SYSTEM.md",
  actor: "agent",
  thread_id: "thread-1",
  turn_id: "turn-1",
  diff: "",
}

function openPanel(answers: Answers) {
  const caller = stubCaller({
    "prompt.get": ({ name }) =>
      name === "behavior"
        ? behavior
        : { content: "The shipped lens.", hash: "empty", default: true },
    "config.history": ({ file }) => ({ changes: file === "SYSTEM.md" ? [byTheAgent] : [] }),
    ...answers,
  })
  render(<ConfigPanel client={caller} />)
  return { caller, user: userEvent.setup() }
}

describe("ConfigPanel", () => {
  it("lists every section in its group, and the ones not built yet as unavailable", async () => {
    openPanel({})

    const sections = screen.getByRole("navigation", { name: "Config sections" })
    const group = (name: string) => within(sections).getByRole("list", { name })
    const labels = (name: string) =>
      within(group(name))
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label"))
    expect(within(sections).getAllByRole("list")).toEqual(
      ["Behavior", "Capabilities", "Instance"].map(group),
    )
    expect(labels("Behavior")).toEqual([
      "Behavior prompt",
      "Recap prompt",
      "Permissions",
      "Manifest",
    ])
    expect(labels("Capabilities")).toEqual(["Routines", "Skills", "Tools"])
    expect(labels("Instance")).toEqual([
      "Secrets and login",
      "Package config",
      "Package and version",
    ])
    expect(screen.getByRole("button", { name: "Behavior prompt" })).toHaveProperty(
      "disabled",
      false,
    )
    expect(screen.getByRole("button", { name: "Recap prompt" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Permissions" })).toHaveProperty("disabled", true)
    expect(screen.getByRole("button", { name: "Package and version" })).toHaveProperty(
      "disabled",
      true,
    )
    expect(await screen.findByRole("heading", { name: "Behavior prompt" })).toBeDefined()
  })

  it("edits the behavior prompt and says who changed it last", async () => {
    const { caller, user } = openPanel({
      "prompt.set": ({ content }) => ({ content, hash: "hash-2", default: false }),
    })

    const editor = await screen.findByRole("textbox", { name: "SYSTEM.md" })
    expect(editor).toHaveProperty("value", "Be brief.\n")
    expect(screen.getByText("Last changed by the agent, Sep 28, 2026, 10:04 AM")).toBeDefined()
    await user.clear(editor)
    await user.type(editor, "Be kind.")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Saved. It applies at the next turn.")).toBeDefined()
    expect(caller.calls.filter((call) => call.method === "prompt.set")).toEqual([
      {
        method: "prompt.set",
        params: { name: "behavior", content: "Be kind.", hash: "hash-1" },
      },
    ])
    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true)
  })

  it("shows when the recap prompt is the shipped default", async () => {
    const { user } = openPanel({})

    await user.click(screen.getByRole("button", { name: "Recap prompt" }))

    expect(await screen.findByRole("textbox", { name: "RECAP.md" })).toHaveProperty(
      "value",
      "The shipped lens.",
    )
    expect(screen.getByText("Shipped default")).toBeDefined()
    expect(screen.getByText("Never changed")).toBeDefined()
  })

  it("offers to load theirs when the prompt changed since it was opened", async () => {
    let theirs = false
    const { caller, user } = openPanel({
      "prompt.get": () =>
        theirs ? { content: "The agent's text.", hash: "hash-3", default: false } : behavior,
      "prompt.set": () => {
        theirs = true
        throw new CallError({ code: "STALE", message: "SYSTEM.md changed.", retryable: false })
      },
    })

    const editor = await screen.findByRole("textbox", { name: "SYSTEM.md" })
    await user.type(editor, "Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

    expect(await screen.findByDisplayValue("The agent's text.")).toBe(editor)
    expect(screen.queryByRole("alert")).toBeNull()
    await user.type(editor, " And mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(caller.calls.filter((call) => call.method === "prompt.set").at(-1)?.params).toEqual({
      name: "behavior",
      content: "The agent's text. And mine.",
      hash: "hash-3",
    })
  })
})
