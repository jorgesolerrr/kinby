import { CallError, type FactoryResult, type FactoryVersion } from "@kinby/contract"
import { type Answers, stubCaller } from "@kinby/contract/testing"
import { screen, waitFor, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { detail } from "@/components/factories-testing"
import { commit, headerHeight, listed, openLibrary } from "@/components/library-testing"

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

const hookFile = "@hook\ndef check_implementation(end):\n    return None\n"

/** The factory as the hub reads it, made of `file` and at `version`. */
function factoryRead(name: string, file: string, version: string | null, hash = "h-2") {
  return {
    name,
    source: "shipped" as const,
    files: {
      "factory.yaml": file,
      "prompts/implement.md": "Implement it.\n",
      "hooks/implementation.py": hookFile,
    },
    hash,
    version,
    declared_in: { check_implementation: "hooks/implementation.py" },
    differs_from_shipped: false,
    shipped_update_waiting: false,
    differing_steps: [],
  }
}

function openPage(file = factoryFile, history = versions, more: Answers = {}) {
  const caller = stubCaller({
    "library.list": () => listed,
    "factory.get": ({ name }) => factoryRead(name, file, history[0]?.version ?? null),
    "factory.version.list": () => ({ versions: history }),
    "factory.version.diff": ({ version }) => ({ diff: `diff of ${version}\n` }),
    "factory.revert": ({ name }) => ({
      name,
      source: "hub",
      files: { "factory.yaml": file },
      hash: "h-3",
      version: shippedVersion,
      declared_in: {},
      differs_from_shipped: false,
      shipped_update_waiting: false,
      differing_steps: [],
    }),
    ...more,
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
    expect(
      screen.getByText("Other files: prompts/implement.md, hooks/implementation.py"),
    ).toBeTruthy()
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

async function openMap(file = software, more: Answers = {}) {
  const { caller, user } = openPage(file, versions, more)
  await user.click(await screen.findByRole("button", { name: "software, shipped factory" }))
  return { caller, user, map: await screen.findByRole("figure", { name: "Map" }) }
}

const height = (map: HTMLElement, label: string) =>
  Number(within(map).getByText(label).getAttribute("y"))

/** Each route back on the map: its arc as points sampled from its path, and its label's box, its
 * width estimated from its characters since jsdom measures no text. */
function routesBack(map: HTMLElement) {
  return Array.from(within(map).getByLabelText("Routes back").querySelectorAll("g"), (route) => {
    const d = route.querySelector("path")?.getAttribute("d") ?? ""
    const [x1, y1, cx1, cy1, cx2, cy2, x2, y2] = Array.from(d.matchAll(/-?[\d.]+/g), Number)
    const points = Array.from({ length: 401 }, (_, step) => {
      const t = step / 400
      const s = 1 - t
      const bezier = (a: number, b: number, c: number, e: number) =>
        s ** 3 * a + 3 * s ** 2 * t * b + 3 * s * t ** 2 * c + t ** 3 * e
      return { x: bezier(x1, cx1, cx2, x2), y: bezier(y1, cy1, cy2, y2) }
    })
    const label = route.querySelector("text")
    const size = Number(label?.getAttribute("font-size"))
    const half = ((label?.textContent ?? "").length * size * 0.6) / 2
    const x = Number(label?.getAttribute("x"))
    const y = Number(label?.getAttribute("y"))
    const box = { left: x - half, right: x + half, top: y - size, bottom: y }
    return { label: label?.textContent, points, box }
  })
}

/** The labels each route back's label meets, by another route back's arc or label. */
function collisions(map: HTMLElement) {
  const routes = routesBack(map)
  type Box = (typeof routes)[number]["box"]
  const inside = ({ x, y }: { x: number; y: number }, box: Box) =>
    box.left <= x && x <= box.right && box.top <= y && y <= box.bottom
  const overlap = (one: Box, other: Box) =>
    one.left < other.right &&
    other.left < one.right &&
    one.top < other.bottom &&
    other.top < one.bottom
  return routes.flatMap((route) =>
    routes
      .filter(
        (other) =>
          other !== route &&
          (other.points.some((point) => inside(point, route.box)) || overlap(route.box, other.box)),
      )
      .map((other) => `${route.label} meets ${other.label}`),
  )
}

describe("LibraryPage, the shipped software factory's map and cards", () => {
  it("draws each route back as an arc above the line, labelled with its outcome and max", async () => {
    const { map } = await openMap()

    const arcs = within(map).getByLabelText("Routes back")
    expect(Array.from(arcs.querySelectorAll("text"), (label) => label.textContent)).toEqual([
      "changes ×2",
      "waiting ×30",
      "failed ×2",
      "feedback ×3",
      "checks-failed ×2",
    ])
    expect(Array.from(arcs.querySelectorAll("path"), (arc) => arc.textContent)).toEqual([
      "review: changes → back to fix, up to 2 times",
      "assess: waiting → back to babysit, up to 30 times",
      "checks: failed → back to fix, up to 2 times",
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
    const { map } = await openMap()

    expect(Math.abs(height(map, "changes ×2") - height(map, "failed ×2"))).toBeGreaterThanOrEqual(
      16,
    )
  })

  it("keeps each route back's label clear of every other route back", async () => {
    const { map } = await openMap()

    expect(collisions(map)).toEqual([])
  })

  it("keeps clear the labels of routes back of different spans into the same step", async () => {
    const { map } = await openMap(`name: software
requires: {}
work_item: { issue: int }
steps:
  - { id: plan, kind: agent, engine: claude, prompt: prompts/implement.md }
  - { id: build, kind: agent, engine: claude, prompt: prompts/implement.md }
  - id: lint
    kind: code
    call: tools/github.py:check
    outcomes: { dirty: { back: build, max: 4 } }
  - { id: test, kind: code, call: tools/github.py:check, failed: { back: build, max: 2 } }
  - { id: ship, kind: code, call: tools/github.py:check }
  - id: watch
    kind: code
    call: tools/github.py:check
    outcomes:
      redo: { back: build, max: 1 }
      replan-from-scratch: { back: plan, max: 3 }
`)

    expect(within(map).getByLabelText("Routes back").querySelectorAll("path")).toHaveLength(4)
    expect(collisions(map)).toEqual([])
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
      "review",
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

    const babysit = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem")[8]
    expect(scrolled.mock.contexts).toEqual([babysit])
  })

  it("scrolls a step's card to below the Library's header, by the header's height", async () => {
    await openMap()

    const babysit = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem")[8]
    const page = babysit.closest<HTMLElement>('[style*="--library-scroll-margin"]')
    expect(page?.style.getPropertyValue("--library-scroll-margin")).toBe(
      `calc(${headerHeight}px + var(--spacing) * 4)`,
    )
    expect(babysit.className).toContain("scroll-mt-(--library-scroll-margin)")
  })
})

const card = (id: string) =>
  within(screen.getByRole("list", { name: "Steps" }))
    .getAllByRole("listitem")
    .find((step) => within(step).getByRole("heading").textContent === id) as HTMLElement

async function openStep(id: string, more: Answers = {}) {
  const opened = await openMap(software, more)
  await opened.user.click(within(card(id)).getByRole("button", { name: id }))
  const form = await within(card(id)).findByRole("form", { name: id })
  return { ...opened, form, editor: within(form) }
}

const labels = (form: HTMLElement) =>
  Array.from(form.querySelectorAll("label"), (label) => label.textContent)

describe("LibraryPage, a step's editor", () => {
  it("opens an agent step's card with its run fields, its prompt and its check", async () => {
    const { form, editor } = await openStep("implement")

    expect(labels(form)).toEqual([
      "Description",
      "Engine",
      "Model",
      "Effort",
      "Timeout",
      "Resume",
      "Retry",
      "When it fails",
      "Outcome no-change",
      "Prompt · prompts/implement.md",
      "Check · hooks/implementation.py",
    ])
    expect((editor.getByRole("textbox", { name: "Model" }) as HTMLInputElement).value).toBe(
      "claude-opus-5-5",
    )
    expect(
      (editor.getByRole("textbox", { name: "Prompt · prompts/implement.md" }) as HTMLInputElement)
        .value,
    ).toBe("Implement it.\n")
    expect(
      (editor.getByRole("textbox", { name: "Check · hooks/implementation.py" }) as HTMLInputElement)
        .value,
    ).toBe(hookFile)
    expect(editor.getByText("branch, base")).toBeTruthy()
    expect(editor.getByText("title: str, body: str, no_change: str")).toBeTruthy()
  })

  it("opens a wait step's card with its quiet time and its signal patterns", async () => {
    const { form, editor } = await openStep("babysit")

    expect(labels(form)).toEqual(["Description", "Quiet", "Retry", "When it fails", "Signal"])
    expect((editor.getByRole("textbox", { name: "Signal" }) as HTMLInputElement).value).toContain(
      "- headers.X-GitHub-Event: pull_request_review\n  body.pull_request.number:",
    )
  })

  it("opens a step's card from its node on the map", async () => {
    Element.prototype.scrollIntoView = vi.fn()
    const { user, map } = await openMap()

    await user.click(within(map).getByRole("link", { name: "assess" }))

    expect(await within(card("assess")).findByRole("form", { name: "assess" })).toBeTruthy()
  })

  it("spells out where each outcome sends the run", async () => {
    const { editor } = await openStep("assess")

    expect(editor.getByText(/^Now:/).textContent).toBe(
      "Now: merge-ready → done; merged → done; closed → stop; " +
        "feedback → back to answer, up to 3 times; " +
        "checks-failed → back to answer, up to 2 times; " +
        "waiting → back to babysit, up to 30 times",
    )
  })

  it("offers a failure route only to earlier steps", async () => {
    const { user, editor } = await openStep("checks")

    await user.click(editor.getByRole("combobox", { name: "When it fails" }))

    const options = (await screen.findAllByRole("option")).map((option) => option.textContent)
    expect(options).toEqual([
      "Retry, then needs a human",
      "Try this step again",
      "Back to ticket",
      "Back to setup",
      "Back to implement",
      "Back to answer",
      "Back to fix",
    ])
  })

  it("saves the step, its prompt and its check against the hash it read", async () => {
    const { caller, user, editor } = await openStep("implement", {
      "factory.step.save": ({ name }) => ({
        name,
        source: "hub",
        files: { "factory.yaml": software },
        hash: "h-3",
        version: commit,
        declared_in: {},
        differs_from_shipped: false,
        shipped_update_waiting: false,
        differing_steps: [],
      }),
    })
    const timeout = editor.getByRole("textbox", { name: "Timeout" })
    await user.clear(timeout)
    await user.type(timeout, "90m")
    await user.type(editor.getByRole("textbox", { name: "Prompt · prompts/implement.md" }), "Now.")

    await user.click(editor.getByRole("button", { name: "Save" }))

    const saved = caller.calls.find((call) => call.method === "factory.step.save")
    expect(saved?.params).toEqual({
      name: "software",
      hash: "h-2",
      step: expect.objectContaining({ id: "implement", timeout: "90m", model: "claude-opus-5-5" }),
      prompt: "Implement it.\nNow.",
      hook: null,
    })
  })

  it("shows the check's problems when the hub refuses a save, and keeps the edit", async () => {
    const { user, editor } = await openStep("checks", {
      "factory.step.save": () => {
        throw new CallError({
          code: "INVALID_ARGUMENT",
          message:
            "The factory check failed, so nothing changed:\n" +
            '- Step "checks" sends its failures back to "publish", which is not an earlier step.',
          retryable: false,
        })
      },
    })
    const retry = editor.getByRole("spinbutton", { name: "Retry" })
    await user.type(retry, "3")

    await user.click(editor.getByRole("button", { name: "Save" }))

    expect(
      await editor.findByText(/Step "checks" sends its failures back to "publish"/),
    ).toBeTruthy()
    expect((retry as HTMLInputElement).value).toBe("3")
  })

  it("saves against the hash its card opened on, though the factory was read again since", async () => {
    let reads = 0
    const { caller, user, editor } = await openStep("implement", {
      "factory.get": ({ name }) => {
        reads += 1
        return factoryRead(name, software, commit, `h-read-${reads}`)
      },
      "factory.step.save": ({ name }) => factoryRead(name, software, commit, "h-saved"),
    })
    const opened = reads

    window.dispatchEvent(new Event("focus"))
    await waitFor(() => expect(reads).toBeGreaterThan(opened))
    await user.type(editor.getByRole("textbox", { name: "Timeout" }), "0")
    await user.click(editor.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(caller.calls.find((call) => call.method === "factory.step.save")?.params).toEqual(
        expect.objectContaining({
          hash: `h-read-${opened}`,
          step: expect.objectContaining({ timeout: "60m0" }),
        }),
      ),
    )
  })

  it("lists the version a save made in History", async () => {
    const saved: FactoryVersion = {
      version: "2".repeat(40),
      time: "2026-11-20T10:00:00Z",
      source: "web",
      steps: ["fix"],
    }
    let history = versions
    const { user, editor } = await openStep("implement", {
      "factory.get": ({ name }) => factoryRead(name, software, history[0].version),
      "factory.version.list": () => ({ versions: history }),
      "factory.step.save": ({ name }) => {
        history = [saved, ...versions]
        return factoryRead(name, software, saved.version, "h-3")
      },
    })
    const rows = () =>
      within(screen.getByRole("list", { name: "History" })).getAllByRole("listitem")
    await waitFor(() => expect(rows()).toHaveLength(2))

    await user.type(editor.getByRole("textbox", { name: "Timeout" }), "0")
    await user.click(editor.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(rows()).toHaveLength(3))
    expect(within(rows()[0]).getByText("fix")).toBeTruthy()
  })
})

const changed = (name: string) => ({
  name,
  source: "hub" as const,
  files: { "factory.yaml": software },
  hash: "h-3",
  version: commit,
  declared_in: {},
  differs_from_shipped: false,
  shipped_update_waiting: false,
  differing_steps: [],
})

describe("LibraryPage, an off step", () => {
  it("is a dashed node and a dashed card, its name struck through, with an Off badge", async () => {
    const { map } = await openMap()

    const node = within(map).getByRole("link", { name: "review" }).querySelector("circle")
    expect(node?.getAttribute("stroke-dasharray")).toBeTruthy()
    const onNode = within(map).getByRole("link", { name: "fix" }).querySelector("circle")
    expect(onNode?.getAttribute("stroke-dasharray")).toBeNull()
    expect(card("review").querySelector("[data-slot=card]")?.getAttribute("data-variant")).toBe(
      "off",
    )
    expect(within(card("review")).getByRole("heading").className).toContain("line-through")
    expect(within(card("review")).getByText("Off")).toBeTruthy()
    expect(within(card("fix")).queryByText("Off")).toBeNull()
  })

  it("turns a step off against the hash it read", async () => {
    const { caller, user, editor } = await openStep("checks", {
      "factory.step.off.set": ({ name }) => changed(name),
    })

    await user.click(editor.getByRole("switch", { name: "On" }))

    await waitFor(() =>
      expect(caller.calls.find((call) => call.method === "factory.step.off.set")?.params).toEqual({
        name: "software",
        hash: "h-2",
        step: "checks",
        off: true,
      }),
    )
  })

  it("keeps the switch off until unsaved edits are saved or discarded", async () => {
    const { user, editor } = await openStep("checks")
    const on = editor.getByRole("switch", { name: "On" })

    await user.type(editor.getByRole("spinbutton", { name: "Retry" }), "3")

    expect(on.getAttribute("data-disabled")).not.toBeNull()
    await user.click(editor.getByRole("button", { name: "Discard" }))
    expect(on.getAttribute("data-disabled")).toBeNull()
  })

  it("turns an off step back on", async () => {
    const { caller, user, editor } = await openStep("review", {
      "factory.step.off.set": ({ name }) => changed(name),
    })

    await user.click(editor.getByRole("switch", { name: "On" }))

    await waitFor(() =>
      expect(
        caller.calls.find((call) => call.method === "factory.step.off.set")?.params,
      ).toMatchObject({ step: "review", off: false }),
    )
  })

  it("sends the next toggle against the factory as the last toggle left it", async () => {
    let toggled = false
    const read = (name: string) => ({
      ...changed(name),
      files: { "factory.yaml": toggled ? software.replace("    off: true\n", "") : software },
      hash: toggled ? "h-3" : "h-2",
    })
    const { caller, user } = await openStep("review", {
      "factory.get": ({ name }) => read(name),
      "factory.step.off.set": ({ name }) => {
        toggled = true
        return read(name)
      },
    })

    await user.click(within(card("review")).getByRole("switch", { name: "On" }))
    await waitFor(() =>
      expect(within(card("review")).queryByRole("button", { name: "Delete" })).toBeNull(),
    )
    await user.click(within(card("review")).getByRole("switch", { name: "On" }))

    await waitFor(() =>
      expect(
        caller.calls
          .filter((call) => call.method === "factory.step.off.set")
          .map((call) => call.params),
      ).toEqual([
        { name: "software", hash: "h-2", step: "review", off: false },
        { name: "software", hash: "h-3", step: "review", off: true },
      ]),
    )
  })

  it("offers Delete only on an off step", async () => {
    const { caller, user, editor } = await openStep("review", {
      "factory.step.delete": ({ name }) => changed(name),
    })

    await user.click(editor.getByRole("button", { name: "Delete" }))

    await waitFor(() =>
      expect(caller.calls.find((call) => call.method === "factory.step.delete")?.params).toEqual({
        name: "software",
        hash: "h-2",
        step: "review",
      }),
    )
    await user.click(within(card("fix")).getByRole("button", { name: "fix" }))
    const fix = await within(card("fix")).findByRole("form", { name: "fix" })
    expect(within(fix).queryByRole("button", { name: "Delete" })).toBeNull()
  })

  it("names each step that keeps a refused step on as a link, and pulses its node", async () => {
    Element.prototype.scrollIntoView = vi.fn()
    const { user, map, editor } = await openStep("fix", {
      "factory.step.off.set": () => {
        throw new CallError({
          code: "INVALID_ARGUMENT",
          message: "The factory check failed, so nothing changed.",
          retryable: false,
          fields: {
            review: 'sends work back to "fix", which is off.',
            checks: 'sends work back to "fix", which is off.',
          },
        })
      },
    })

    await user.click(editor.getByRole("switch", { name: "On" }))

    const alert = await within(card("fix")).findByRole("alert")
    expect(within(alert).getByText("The factory check keeps fix on")).toBeTruthy()
    expect(alert.textContent).toContain('checks sends work back to "fix", which is off.')
    const pulsing = (id: string) =>
      within(map).getByRole("link", { name: id }).querySelector("circle")?.getAttribute("class")
    expect(pulsing("checks")).toContain("animate-pulse")
    expect(pulsing("review")).toContain("animate-pulse")
    expect(pulsing("answer")).not.toContain("animate-pulse")
    await user.click(within(alert).getByRole("link", { name: "checks" }))
    expect(await within(card("checks")).findByRole("form", { name: "checks" })).toBeTruthy()
  })
})

async function openAdd(after: string, more: Answers = {}) {
  const opened = await openMap(software, {
    "factory.step.add": ({ name }) => changed(name),
    ...more,
  })
  await opened.user.click(
    within(card(after)).getByRole("button", { name: `Add a step after ${after}` }),
  )
  const form = await within(card(after)).findByRole("form", { name: `New step after ${after}` })
  return { ...opened, form: within(form) }
}

const added = (caller: { calls: { method: string; params: unknown }[] }) =>
  caller.calls.find((call) => call.method === "factory.step.add")?.params

describe("LibraryPage, adding a step", () => {
  it("offers an agent, a command or a wait after a step, and never code", async () => {
    const { form } = await openAdd("implement")

    const kinds = form.getByRole("group", { name: "Kind" })
    expect(
      within(kinds)
        .getAllByRole("button")
        .map((kind) => kind.textContent),
    ).toEqual(["agent", "command", "wait"])
    expect(within(kinds).getByRole("button", { name: "agent", pressed: true })).toBeTruthy()
  })

  it("adds an agent step with a new prompt and no check after its step", async () => {
    const { caller, user, form } = await openAdd("implement")

    await user.type(form.getByRole("textbox", { name: "Id" }), "docs")
    await user.type(form.getByRole("textbox", { name: "Prompt · prompts/docs.md" }), "Update.")
    await user.click(form.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(added(caller)).toEqual({
        name: "software",
        hash: "h-2",
        after: "implement",
        step: { id: "docs", kind: "agent", engine: "claude", prompt: "prompts/docs.md" },
        prompt: "Update.",
      }),
    )
  })

  it("gives an agent step with an existing check what that check takes and records", async () => {
    const { caller, user, form } = await openAdd("fix")

    await user.type(form.getByRole("textbox", { name: "Id" }), "docs")
    await user.click(form.getByRole("combobox", { name: "Check" }))
    const offered = await screen.findAllByRole("option")
    expect(offered.map((option) => option.textContent)).toEqual([
      "No check",
      "check_implementation",
      "check_answers",
      "read_review_verdict",
    ])
    await user.click(screen.getByRole("option", { name: "check_implementation" }))
    await user.click(form.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(added(caller)).toMatchObject({
        step: {
          id: "docs",
          check: "check_implementation",
          requires: ["branch", "base"],
          results: { title: "str", body: "str", no_change: "str" },
          outcomes: { "no-change": "stop" },
        },
      }),
    )
  })

  it("adds a command step that runs the workbench's command lists it picks", async () => {
    const { caller, user, form } = await openAdd("checks")

    await user.click(form.getByRole("button", { name: "command" }))
    await user.type(form.getByRole("textbox", { name: "Id" }), "recheck")
    expect(form.getByRole("checkbox", { name: "setup" })).toBeTruthy()
    await user.click(form.getByRole("checkbox", { name: "checks" }))
    await user.click(form.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(added(caller)).toEqual({
        name: "software",
        hash: "h-2",
        after: "checks",
        step: { id: "recheck", kind: "command", run: [{ commands: "checks" }] },
        prompt: null,
      }),
    )
  })

  it("adds a wait step with its signal and its quiet time", async () => {
    const { caller, user, form } = await openAdd("publish")

    await user.click(form.getByRole("button", { name: "wait" }))
    await user.type(form.getByRole("textbox", { name: "Id" }), "settle")
    await user.type(form.getByRole("textbox", { name: "Signal" }), "- routine: github")
    await user.type(form.getByRole("textbox", { name: "Quiet" }), "5m")
    await user.click(form.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(added(caller)).toMatchObject({
        after: "publish",
        step: { id: "settle", kind: "wait", signal: [{ routine: "github" }], quiet: "5m" },
        prompt: null,
      }),
    )
  })

  it("adds against the hash its card opened on, keeping what was typed across a re-read", async () => {
    let reads = 0
    const { caller, user, form } = await openAdd("implement", {
      "factory.get": ({ name }) => {
        reads += 1
        return factoryRead(name, software, commit, `h-read-${reads}`)
      },
    })
    const opened = reads
    await user.type(form.getByRole("textbox", { name: "Id" }), "docs")

    window.dispatchEvent(new Event("focus"))
    await waitFor(() => expect(reads).toBeGreaterThan(opened))
    await user.click(form.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(added(caller)).toMatchObject({ hash: `h-read-${opened}`, step: { id: "docs" } }),
    )
  })

  it("shows the check's problems when the hub refuses an add, and keeps the new step", async () => {
    const { user, form } = await openAdd("implement", {
      "factory.step.add": () => {
        throw new CallError({
          code: "INVALID_ARGUMENT",
          message:
            "The factory check failed, so nothing changed:\n" +
            '- Step "docs" resumes "fix", which is not an earlier step.',
          retryable: false,
        })
      },
    })
    const id = form.getByRole("textbox", { name: "Id" })
    await user.type(id, "docs")

    await user.click(form.getByRole("button", { name: "Add" }))

    expect(await form.findByText(/Step "docs" resumes "fix"/)).toBeTruthy()
    expect((id as HTMLInputElement).value).toBe("docs")
  })
})

describe("LibraryPage, a shipped factory the user changed", () => {
  const read = (marks: Partial<FactoryResult>) => ({
    "factory.get": ({ name }: { name: string }) => ({ ...changed(name), ...marks }),
  })

  it("shows that it differs from shipped and that kinby's update waits", async () => {
    await openMap(software, read({ differs_from_shipped: true, shipped_update_waiting: true }))

    expect(screen.getByText("Differs from shipped")).toBeTruthy()
    expect(screen.getByText("Update waiting")).toBeTruthy()
  })

  it("shows neither mark on kinby's version", async () => {
    await openMap()

    expect(screen.queryByText("Differs from shipped")).toBeNull()
    expect(screen.queryByText("Update waiting")).toBeNull()
  })

  it("offers kinby's version on each step that differs, and takes it against the hash it read", async () => {
    const { caller, user, editor } = await openStep("review", {
      ...read({ differs_from_shipped: true, differing_steps: ["review"] }),
      "factory.step.reset": ({ name }) => changed(name),
    })

    expect(within(card("review")).getByText("Differs from kinby's")).toBeTruthy()
    expect(within(card("fix")).queryByText("Differs from kinby's")).toBeNull()
    await user.click(editor.getByRole("button", { name: "Take kinby's" }))

    await waitFor(() =>
      expect(caller.calls.find((call) => call.method === "factory.step.reset")?.params).toEqual({
        name: "software",
        hash: "h-3",
        step: "review",
      }),
    )
  })

  it("offers nothing to take on a step that is kinby's", async () => {
    const { editor } = await openStep("fix", read({ differing_steps: ["review"] }))

    expect(editor.queryByRole("button", { name: "Take kinby's" })).toBeNull()
  })
})

describe("LibraryPage, a step from its run", () => {
  const fromRun = "/library/factories?factory=software&step=implement&run=r-stopped"

  function openFromRun(more: Answers = {}) {
    Element.prototype.scrollIntoView = vi.fn()
    const caller = stubCaller({
      "library.list": () => listed,
      "factory.get": ({ name }) => factoryRead(name, software, commit),
      "factory.version.list": () => ({ versions }),
      "factory.run.get": () => detail("r-stopped"),
      ...more,
    })
    return { caller, user: openLibrary(caller, fromRun) }
  }

  it("opens the step's card under a bar naming the run its attempt failed in", async () => {
    openFromRun()

    await screen.findByRole("list", { name: "Steps" })
    const form = await within(card("implement")).findByRole("form", { name: "implement" })
    const bar = await within(form).findByRole("alert")
    expect(within(bar).getByText("From the run on issue 1305")).toBeTruthy()
    expect(within(bar).getByText(/^Its implement attempt failed\./)).toBeTruthy()
    expect(within(bar).getByRole("link", { name: "Back to the run" }).getAttribute("href")).toBe(
      "/factories/runs/r-stopped",
    )
  })

  it("saves the step and retries the run on the new version, then opens the run", async () => {
    const { caller, user } = openFromRun({
      "factory.step.save": ({ name }) => factoryRead(name, software, commit, "h-3"),
      "factory.run.retry": () => detail("r-stopped").run,
    })
    await screen.findByRole("list", { name: "Steps" })
    const form = await within(card("implement")).findByRole("form", { name: "implement" })
    const editor = within(form)
    await editor.findByRole("alert")
    await user.type(editor.getByRole("textbox", { name: "Prompt · prompts/implement.md" }), "Now.")

    await user.click(editor.getByRole("button", { name: "Save and retry" }))

    await waitFor(() => expect(window.location.pathname).toBe("/factories/runs/r-stopped"))
    expect(caller.calls.map(({ method }) => method)).toContain("factory.step.save")
    expect(caller.calls.at(-1)).toEqual({
      method: "factory.run.retry",
      params: { run_id: "r-stopped", latest: true },
    })
  })
})
