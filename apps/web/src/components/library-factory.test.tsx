import type { FactoryVersion } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { screen, waitFor, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { commit, listed, openLibrary } from "@/components/library-testing"

const factoryFile = `name: software
requires: { image: coder }
work_item: { issue: int }
steps:
  - { id: check, kind: code, call: tools/github.py:check }
  - { id: implement, kind: agent, engine: claude, prompt: prompts/implement.md }
  - { id: publish, kind: code, call: tools/github.py:publish }
`

const shippedVersion = "1".repeat(40)

const versions: FactoryVersion[] = [
  { version: commit, time: "2026-11-19T10:00:00Z", source: "web", steps: ["implement"] },
  {
    version: shippedVersion,
    time: "2026-11-18T10:00:00Z",
    source: "shipped",
    steps: ["check", "implement", "publish"],
  },
]

function openPage(file = factoryFile, history = versions) {
  const caller = stubCaller({
    "library.list": () => listed,
    "factory.get": ({ name }) => ({
      name,
      source: "shipped",
      files: { "factory.yaml": file, "prompts/implement.md": "Implement it.\n" },
      hash: "h-2",
      version: history[0]?.version ?? null,
    }),
    "factory.version.list": () => ({ versions: history }),
    "factory.version.diff": ({ version }) => ({ diff: `diff of ${version}\n` }),
    "factory.revert": ({ name }) => ({
      name,
      source: "hub",
      files: { "factory.yaml": file },
      hash: "h-3",
      version: shippedVersion,
    }),
  })
  return { caller, user: openLibrary(caller, "/library/factories") }
}

const steps = () =>
  within(screen.getByRole("list", { name: "Steps" }))
    .getAllByRole("listitem")
    .map((step) => step.textContent)

describe("LibraryPage, a factory", () => {
  it("unfolds a factory to read its factory file", async () => {
    const { user } = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const file = await screen.findByRole("textbox", { name: "factory.yaml" })
    expect((file as HTMLTextAreaElement).value).toBe(factoryFile)
    expect(file.hasAttribute("readonly")).toBe(true)
    expect(screen.getByText("Other files: prompts/implement.md")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull()
  })

  it("unfolds a factory to show its steps", async () => {
    const { user } = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    await screen.findByRole("list", { name: "Steps" })
    expect(steps()).toEqual(["check", "implement", "publish"])
  })

  it("reads a step's merge keys as the hub does", async () => {
    const { user } = openPage(`name: software
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

  it("lists the factory's versions newest first, with each one's source and steps", async () => {
    const { user } = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const rows = within(await screen.findByRole("list", { name: "History" })).getAllByRole(
      "listitem",
    )
    expect(rows.map((row) => within(row).getByText(/version|web/).textContent)).toEqual([
      "Saved from the web",
      "kinby's version",
    ])
    expect(within(rows[0]).getByText("implement")).toBeTruthy()
    expect(within(rows[1]).getByText("check, implement, publish")).toBeTruthy()
  })

  it("says a factory kinby ships as it is has no versions yet", async () => {
    const { user } = openPage(factoryFile, [])

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    expect(await screen.findByText(/kinby's version as it ships/)).toBeTruthy()
    expect(screen.queryByRole("list", { name: "History" })).toBeNull()
  })

  it("shows one version's diff", async () => {
    const { caller, user } = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))
    const rows = within(await screen.findByRole("list", { name: "History" })).getAllByRole(
      "listitem",
    )
    await user.click(within(rows[1]).getByRole("button", { name: "Diff" }))

    const diff = await within(rows[1]).findByRole("textbox", { name: "Diff" })
    expect((diff as HTMLTextAreaElement).value).toBe(`diff of ${shippedVersion}\n`)
    expect(caller.calls.filter((call) => call.method === "factory.version.diff")).toEqual([
      { method: "factory.version.diff", params: { name: "software", version: shippedVersion } },
    ])
  })

  it("reverts to an earlier version, then reads the factory and its history again", async () => {
    const { caller, user } = openPage()

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))
    const rows = within(await screen.findByRole("list", { name: "History" })).getAllByRole(
      "listitem",
    )
    expect(within(rows[0]).queryByRole("button", { name: "Revert" })).toBeNull()
    const reads = caller.calls.length
    await user.click(within(rows[1]).getByRole("button", { name: "Revert" }))

    await waitFor(() =>
      expect(caller.calls.slice(reads).map((call) => call.method)).toEqual(
        expect.arrayContaining(["factory.revert", "factory.get", "factory.version.list"]),
      ),
    )
    expect(caller.calls.find((call) => call.method === "factory.revert")?.params).toEqual({
      name: "software",
      version: shippedVersion,
    })
  })
})
