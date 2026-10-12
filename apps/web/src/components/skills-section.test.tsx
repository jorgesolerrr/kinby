import { CallError } from "@kinby/contract"
import type {
  LibraryEntry,
  SkillListResult,
  SkillPick,
  SkillResult,
  SkillSummary,
} from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { SkillsSection } from "@/components/skills-section"

const planning: SkillSummary = {
  name: "planning",
  tier: "instance",
  description: "Plan my way.",
  shadowed_by: null,
}
const kinbyPlanning: SkillSummary = {
  name: "planning",
  tier: "kinby",
  description: "Plan the kinby way.",
  shadowed_by: "instance",
}
const review: SkillSummary = {
  name: "review",
  tier: "kinby",
  description: "Review the kinby way.",
  shadowed_by: null,
}
const workspaceReview: SkillSummary = {
  name: "review",
  tier: "workspace",
  description: "Review the workspace way.",
  shadowed_by: "kinby",
}

const listed: SkillListResult = {
  skills: [planning, kinbyPlanning, review, workspaceReview],
  warnings: [],
}

const skillText = (name: string, body: string) =>
  `---\nname: ${name}\ndescription: ${body}\n---\n${body}\n`

const read: Record<string, SkillResult> = {
  "planning instance": { content: skillText("planning", "Plan my way."), files: [], hash: "h-1" },
  "planning kinby": {
    content: skillText("planning", "Plan the kinby way."),
    files: [],
    hash: "h-2",
  },
  "review kinby": {
    content: skillText("review", "Review the kinby way."),
    files: ["checklist.md"],
    hash: "h-3",
  },
  "review workspace": {
    content: skillText("review", "Review the workspace way."),
    files: [],
    hash: "h-4",
  },
}

function openSection(answers: Answers) {
  const caller = stubCaller({
    "skill.list": () => listed,
    "skill.read": ({ name, tier }) => read[`${name} ${tier}`],
    "config.history": () => ({ changes: [] }),
    "instance.picks.get": () => ({ picks: [] }),
    "library.list": () => ({ entries: [] }),
    ...answers,
  })
  render(
    <SkillsSection client={caller} caller={caller} clock={fakeClock()} instanceId="instance-1" />,
  )
  return { caller, user: userEvent.setup() }
}

const called = (caller: ReturnType<typeof openSection>["caller"], method: string) =>
  caller.calls.filter((call) => call.method === method).map((call) => call.params)

describe("SkillsSection", () => {
  it("lists each skill with its tier, the shadowed ones under the one that wins", async () => {
    openSection({})

    const list = await screen.findByRole("list", { name: "Skills" })
    const items = within(list).getAllByRole("button")
    expect(items.map((item) => item.getAttribute("aria-label"))).toEqual([
      "planning, instance skill",
      "planning, kinby skill",
      "review, kinby skill",
      "review, workspace skill",
    ])
    expect(items.map((item) => item.dataset.variant)).toEqual([
      "default",
      "muted",
      "default",
      "muted",
    ])
    expect(within(items[1]).getByText("Hidden by the instance skill")).toBeDefined()
    expect(within(items[3]).getByText("Hidden by the kinby skill")).toBeDefined()
  })

  it("shows no list marker beside a skill, and indents the shadowed ones", async () => {
    openSection({})

    const list = await screen.findByRole("list", { name: "Skills" })
    const items = within(list).getAllByRole("listitem")
    // jsdom applies no Tailwind, so the classes stand in for the computed style.
    expect(items.map((item) => item.classList.contains("list-none"))).toEqual([
      true,
      true,
      true,
      true,
    ])
    expect(items.map((item) => item.classList.contains("pl-6"))).toEqual([false, true, false, true])
  })

  it("left-aligns every line of a skill row, since a button centers its text", async () => {
    openSection({})

    const list = await screen.findByRole("list", { name: "Skills" })
    // jsdom applies no Tailwind, so the class stands in for the computed alignment.
    expect(
      within(list)
        .getAllByRole("button")
        .map((button) => button.classList.contains("text-left")),
    ).toEqual([true, true, true, true])
  })

  it("reads a kinby skill as read-only and customizes it into the instance", async () => {
    let customized = false
    const { caller, user } = openSection({
      "skill.list": () =>
        customized
          ? {
              skills: [
                planning,
                kinbyPlanning,
                { ...review, tier: "instance" },
                { ...review, shadowed_by: "instance" },
                { ...workspaceReview, shadowed_by: "instance" },
              ],
              warnings: [],
            }
          : listed,
      "skill.customize": () => {
        customized = true
        return { ...read["review kinby"], hash: "h-5" }
      },
      "skill.read": ({ name, tier }) =>
        read[`${name} ${tier}`] ?? { ...read["review kinby"], hash: "h-5" },
    })

    await user.click(await screen.findByRole("button", { name: "review, kinby skill" }))
    const viewer = await screen.findByRole("textbox", { name: "SKILL.md" })
    expect(viewer).toHaveProperty("readOnly", true)
    expect(viewer).toHaveProperty("value", read["review kinby"].content)
    expect(screen.getByText("Other files: checklist.md")).toBeDefined()
    await user.click(screen.getByRole("button", { name: "Customize" }))

    const editor = await screen.findByRole("button", { name: "Remove customization" })
    expect(editor).toBeDefined()
    expect(screen.getByRole("textbox", { name: "SKILL.md" })).toHaveProperty("readOnly", false)
    expect(called(caller, "skill.customize")).toEqual([{ name: "review" }])
    expect(
      screen.getByRole("button", { name: "review, instance skill" }).getAttribute("aria-current"),
    ).toBe("true")
  })

  it("offers no Customize on a skill another tier hides", async () => {
    const { user } = openSection({})

    await user.click(await screen.findByRole("button", { name: "review, workspace skill" }))

    expect(await screen.findByRole("textbox", { name: "SKILL.md" })).toHaveProperty(
      "readOnly",
      true,
    )
    expect(
      screen.getByText("The kinby skill hides this one, so the model reads that."),
    ).toBeDefined()
    expect(screen.queryByRole("button", { name: "Customize" })).toBeNull()
  })

  it("edits an instance skill with the hash it read", async () => {
    const { caller, user } = openSection({
      "skill.write": ({ content }) => ({ content, files: [], hash: "h-6" }),
    })

    await user.click(await screen.findByRole("button", { name: "planning, instance skill" }))
    const editor = await screen.findByRole("textbox", { name: "SKILL.md" })
    await user.type(editor, "Step by step.")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Saved. It applies at the next turn.")).toBeDefined()
    expect(called(caller, "skill.write")).toEqual([
      {
        name: "planning",
        content: `${read["planning instance"].content}Step by step.`,
        hash: "h-1",
      },
    ])
  })

  it("shows what the instance refused in an instance skill next to the editor", async () => {
    const { user } = openSection({
      "skill.write": () => {
        throw new CallError({
          code: "INVALID_ARGUMENT",
          message: "Skill frontmatter is missing.",
          retryable: false,
          fields: { content: "Skill frontmatter is missing." },
        })
      },
    })

    await user.click(await screen.findByRole("button", { name: "planning, instance skill" }))
    const editor = await screen.findByRole("textbox", { name: "SKILL.md" })
    await user.clear(editor)
    await user.type(editor, "No frontmatter.")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Skill frontmatter is missing.")).toBeDefined()
    expect(editor.getAttribute("aria-invalid")).toBe("true")
  })

  it("offers to load theirs when the skill changed since it was opened", async () => {
    let theirs = false
    const { user } = openSection({
      "skill.read": ({ name, tier }) =>
        theirs
          ? { content: skillText("planning", "The agent's plan."), files: [], hash: "h-7" }
          : read[`${name} ${tier}`],
      "skill.write": () => {
        theirs = true
        throw new CallError({
          code: "STALE",
          message: "skills/planning changed.",
          retryable: false,
        })
      },
    })

    await user.click(await screen.findByRole("button", { name: "planning, instance skill" }))
    await user.type(await screen.findByRole("textbox", { name: "SKILL.md" }), "Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

    expect(
      await screen.findByDisplayValue(skillText("planning", "The agent's plan.").trim(), {
        normalizer: (text) => text.trim(),
      }),
    ).toBeDefined()
  })

  it("removes a customization and shows the skill it hid", async () => {
    const { caller, user } = openSection({
      "skill.delete": () => ({
        skills: [{ ...kinbyPlanning, shadowed_by: null }, review, workspaceReview],
        warnings: [],
      }),
    })

    await user.click(await screen.findByRole("button", { name: "planning, instance skill" }))
    await user.click(await screen.findByRole("button", { name: "Remove customization" }))

    expect(called(caller, "skill.delete")).toEqual([{ name: "planning", hash: "h-1" }])
    expect(await screen.findByDisplayValue(/Plan the kinby way\./)).toBeDefined()
    expect(screen.queryByRole("button", { name: "planning, instance skill" })).toBeNull()
    expect(
      screen.getByRole("button", { name: "planning, kinby skill" }).getAttribute("aria-current"),
    ).toBe("true")
  })

  it("creates an instance skill from its name, description, and instructions", async () => {
    const outlining: SkillSummary = {
      name: "outlining",
      tier: "instance",
      description: "Outline first.",
      shadowed_by: null,
    }
    let created = false
    const { caller, user } = openSection({
      "skill.list": () =>
        created ? { skills: [outlining, ...listed.skills], warnings: [] } : listed,
      "skill.write": ({ content }) => {
        created = true
        return { content, files: [], hash: "h-8" }
      },
      "skill.read": ({ name, tier }) =>
        name === "outlining"
          ? { content: skillText("outlining", "Outline first."), files: [], hash: "h-8" }
          : read[`${name} ${tier}`],
    })

    await user.click(await screen.findByRole("button", { name: "New skill" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "outlining")
    await user.type(screen.getByRole("textbox", { name: "Description" }), "Outline first.")
    await user.type(screen.getByRole("textbox", { name: "Instructions" }), "Outline first.")
    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(called(caller, "skill.write")).toEqual([
      { name: "outlining", content: skillText("outlining", "Outline first."), hash: null },
    ])
    expect(
      (await screen.findByRole("button", { name: "outlining, instance skill" })).getAttribute(
        "aria-current",
      ),
    ).toBe("true")
    expect(screen.getByRole("button", { name: "Delete" })).toBeDefined()
  })

  describe("picks from the library", () => {
    const tdd: SkillSummary = {
      name: "tdd",
      tier: "instance",
      description: "Test first.",
      shadowed_by: null,
    }
    const library: LibraryEntry[] = [
      { kind: "factory", name: "software", description: null, source: "shipped" },
      { kind: "skill", name: "tdd", description: "Test first.", source: "shipped" },
      { kind: "skill", name: "unslop", description: "Cut AI tells.", source: "shipped" },
    ]
    const pickedTdd: SkillPick = { name: "tdd", source: "shipped" }

    function openWithPicks(answers: Answers = {}) {
      let picks = [pickedTdd]
      return openSection({
        "skill.list": () => ({ skills: [planning, tdd], warnings: [] }),
        "instance.picks.get": () => ({ picks }),
        "library.list": () => ({ entries: library }),
        "instance.picks.set": ({ skills }) => {
          picks = skills.map((name) => ({ name, source: "shipped" }))
          return { picks }
        },
        ...answers,
      })
    }

    it("shows the picks apart from the agent's own skills", async () => {
      openWithPicks()

      const picks = await screen.findByRole("list", { name: "Picks" })
      expect(within(picks).getByText("tdd")).toBeDefined()
      const skills = screen.getByRole("list", { name: "Skills" })
      expect(
        within(skills)
          .getAllByRole("button")
          .map((item) => item.getAttribute("aria-label")),
      ).toEqual(["planning, instance skill", "tdd, picked skill"])
    })

    it("offers the library's other skills and adds one to the picks", async () => {
      const { caller, user } = openWithPicks()

      const offered = await screen.findByRole("list", { name: "Library skills" })
      expect(within(offered).queryByText("tdd")).toBeNull()
      expect(within(offered).queryByText("software")).toBeNull()
      await user.click(within(offered).getByRole("button", { name: "Add unslop" }))

      expect(called(caller, "instance.picks.set")).toEqual([
        { instance_id: "instance-1", skills: ["tdd", "unslop"] },
      ])
      const picks = screen.getByRole("list", { name: "Picks" })
      expect(await within(picks).findByText("unslop")).toBeDefined()
      expect(called(caller, "skill.list").length).toBeGreaterThan(1)
    })

    it("drops a pick", async () => {
      const { caller, user } = openWithPicks()

      await user.click(await screen.findByRole("button", { name: "Drop tdd" }))

      expect(called(caller, "instance.picks.set")).toEqual([
        { instance_id: "instance-1", skills: [] },
      ])
      expect(await screen.findByText("The instance has picked no library skill.")).toBeDefined()
    })

    it("says why the hub refused a pick", async () => {
      const { user } = openWithPicks({
        "instance.picks.set": () => {
          throw new CallError({
            code: "INVALID_ARGUMENT",
            message: "Some values are invalid.",
            retryable: false,
            fields: { skills: 'The instance has its own skill "unslop".' },
          })
        },
      })

      await user.click(await screen.findByRole("button", { name: "Add unslop" }))

      expect((await screen.findByRole("alert")).textContent).toContain(
        'The instance has its own skill "unslop".',
      )
    })
  })
})
