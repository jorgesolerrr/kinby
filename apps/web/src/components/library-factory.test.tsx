import type { FactoryVersion } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { screen, waitFor, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { commit, listed, openLibrary } from "@/components/library-testing"

import software from "../../../../src/kinby/factories/shipped/software/factory.yaml?raw"

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
    .map((step) => within(step).getByRole("heading").textContent)

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

  it("reads what a step declares as null as absent", async () => {
    const { user } = openPage(`name: software
requires: {}
work_item: { issue: int }
steps:
  - { id: implement, kind: agent, engine: claude, prompt: prompts/implement.md }
  - id: fix
    kind: agent
    engine: claude
    prompt: prompts/fix.md
    description: null
    resume: null
    failed:
`)

    await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))

    const map = await screen.findByRole("figure", { name: "Map" })
    expect(within(map).getByLabelText("Routes back").querySelector("path")).toBeNull()
    expect(within(map).getByLabelText("Resumed sessions").querySelector("path")).toBeNull()
    expect(steps()).toEqual(["implement", "fix"])
    const [, fix] = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem")
    expect(fix.querySelector("p")).toBeNull()
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

/** The shipped software factory's file with its review step uncommented, as its own tests do. */
const withReview = software.replace(/^ {2}# (?=- | {2})/gm, "  ")

async function openMap(file = software) {
  const { user } = openPage(file)
  await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))
  return { user, map: await screen.findByRole("figure", { name: "Map" }) }
}

const height = (map: HTMLElement, label: string) =>
  Number(within(map).getByText(label).getAttribute("y"))

describe("LibraryPage, the shipped software factory's map and cards", () => {
  it("draws each route back as an arc above the line, labelled with its outcome and max", async () => {
    const { map } = await openMap()

    const arcs = within(map).getByLabelText("Routes back")
    expect(Array.from(arcs.querySelectorAll("text"), (label) => label.textContent)).toEqual([
      "failed ×2",
      "waiting ×30",
      "feedback ×3",
      "checks-failed ×2",
    ])
    expect(Array.from(arcs.querySelectorAll("path"), (arc) => arc.textContent)).toEqual([
      "checks: failed → back to fix, up to 2 times",
      "assess: waiting → back to babysit, up to 30 times",
      "assess: feedback → back to answer, up to 3 times",
      "assess: checks-failed → back to answer, up to 2 times",
    ])
    const line = Number(map.querySelector("line")?.getAttribute("y1"))
    for (const label of arcs.querySelectorAll("text")) {
      expect(Number(label.getAttribute("y"))).toBeLessThan(line)
    }
  })

  it("stacks two routes between the same steps as separate arcs", async () => {
    const { map } = await openMap()

    expect(height(map, "feedback ×3")).not.toBe(height(map, "checks-failed ×2"))
  })

  it("keeps apart the labels of review's route and checks' failed route to fix", async () => {
    const { map } = await openMap(withReview)

    expect(Math.abs(height(map, "changes ×2") - height(map, "failed ×2"))).toBeGreaterThanOrEqual(
      16,
    )
  })

  it("draws each resumed session as a dotted arc below the line", async () => {
    const { map } = await openMap()

    const arcs = within(map).getByLabelText("Resumed sessions")
    const resumes = arcs.querySelectorAll("path")
    expect(Array.from(resumes, (arc) => arc.textContent)).toEqual([
      "answer resumes implement's session",
      "fix resumes implement's session",
    ])
    const line = Number(map.querySelector("line")?.getAttribute("y1"))
    for (const arc of resumes) {
      expect(arc.getAttribute("stroke-dasharray")).toBeTruthy()
      const [, start] = /^M[\d.]+,([\d.]+)/.exec(arc.getAttribute("d") ?? "") ?? []
      expect(Number(start)).toBeGreaterThan(line)
    }
  })

  it("shows a card per step with its description, kind, engine and model", async () => {
    await openMap()

    expect(steps()).toEqual([
      "ticket",
      "setup",
      "implement",
      "answer",
      "fix",
      "checks",
      "publish",
      "babysit",
      "assess",
    ])
    const [ticket, , implement] = within(screen.getByRole("list", { name: "Steps" })).getAllByRole(
      "listitem",
    )
    expect(within(implement).getByText("agent · claude · claude-opus-5-5")).toBeTruthy()
    expect(
      within(implement).getByText(/^Every agent step continues implement's session/),
    ).toBeTruthy()
    expect(within(ticket).getByText("code")).toBeTruthy()
    expect(
      within(ticket).getByText(/^Passes the ticket on only once a trusted author wrote/),
    ).toBeTruthy()
    expect(within(ticket).queryByText(/claude/)).toBeNull()
  })

  it("scrolls to a step's card when its node on the map is clicked", async () => {
    const scrolled = vi.fn()
    Element.prototype.scrollIntoView = scrolled
    const { user, map } = await openMap()

    await user.click(within(map).getByRole("link", { name: "babysit" }))

    const babysit = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem")[7]
    expect(scrolled.mock.contexts).toEqual([babysit])
  })
})
