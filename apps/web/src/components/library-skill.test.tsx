import { CallError } from "@kinby/contract"
import type { LibraryEntry, LibraryGitPreviewResult } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { screen, waitFor, within } from "@testing-library/react"
import type { UserEvent } from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { commit, listed, names, openLibrary, refused, tdd } from "@/components/library-testing"

/** Open New skill's form `form`. */
async function newSkill(user: UserEvent, form: "Write a skill" | "Add from git") {
  await user.click(await screen.findByRole("button", { name: "New skill" }))
  await user.click(await screen.findByRole("menuitem", { name: form }))
}

describe("LibraryPage, a skill", () => {
  it("unfolds a skill's SKILL.md to edit and lists its other files", async () => {
    const caller = stubCaller({
      "library.list": () => listed,
      "library.skill.read": ({ name }) => ({
        content: name === "tdd" ? tdd : "",
        files: ["CREDITS.md", "LICENSE", "tests.md"],
        hash: "h-1",
      }),
    })
    const user = openLibrary(caller)

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))

    const body = await screen.findByRole("textbox", { name: "SKILL.md" })
    expect((body as HTMLTextAreaElement).value).toBe(tdd)
    expect(body.hasAttribute("readonly")).toBe(false)
    expect(screen.getByText("Other files: CREDITS.md, LICENSE, tests.md")).toBeTruthy()
    expect(caller.calls.filter((call) => call.method === "library.skill.read")).toEqual([
      { method: "library.skill.read", params: { name: "tdd" } },
    ])
  })
})

const grill = "---\nname: grill\ndescription: Ask why.\n---\nAsk why five times.\n"

const shown: LibraryGitPreviewResult = {
  url: "https://github.com/owner/repo.git",
  ref: "main",
  commit,
  skills: [
    { name: "caveman", path: "skills/caveman", content: "Talk short.\n", files: [] },
    { name: "grill", path: "skills/grill", content: grill, files: ["references/questions.md"] },
  ],
}

const added: LibraryEntry = {
  kind: "skill",
  name: "grill",
  description: "Ask why.",
  source: "git",
  git: { url: shown.url, path: "skills/grill", ref: "main", commit },
}

function openGitPage(preview: () => LibraryGitPreviewResult) {
  let entries = listed.entries
  const caller = stubCaller({
    "library.list": () => ({ entries }),
    "library.skill.read": () => ({ content: grill, files: [], hash: "h-1" }),
    "library.git.preview": preview,
    "library.git.add": () => {
      entries = [...entries, added]
      return { entries: [added] }
    },
  })
  return { caller, user: openLibrary(caller) }
}

describe("LibraryPage, adding from git", () => {
  it("walks the address, the preview and the confirm, then lists the entry with its commit", async () => {
    const { caller, user } = openGitPage(() => shown)

    await newSkill(user, "Add from git")
    await user.type(screen.getByRole("textbox", { name: "Address" }), "owner/repo#main")
    await user.click(screen.getByRole("button", { name: "Preview" }))

    expect(await screen.findByText(`At commit ${commit}`)).toBeTruthy()
    const body = screen.getByRole("textbox", { name: "grill SKILL.md" })
    expect((body as HTMLTextAreaElement).value).toBe(grill)
    expect(screen.getByText("Other files: references/questions.md")).toBeTruthy()
    expect(caller.calls.some((call) => call.method === "library.git.add")).toBe(false)

    await user.click(screen.getByRole("checkbox", { name: "caveman" }))
    await user.click(screen.getByRole("button", { name: "Add 1 skill" }))

    await user.click(await screen.findByRole("button", { name: "grill, git skill" }))
    expect(await screen.findByText(`${shown.url} at ${commit}`)).toBeTruthy()
    expect(caller.calls.filter((call) => call.method.startsWith("library.git"))).toEqual([
      { method: "library.git.preview", params: { address: "owner/repo#main" } },
      {
        method: "library.git.add",
        params: { address: "owner/repo#main", commit, skills: ["grill"] },
      },
    ])
    expect(screen.queryByRole("textbox", { name: "Address" })).toBeNull()
  })

  it("shows why the hub refused an address", async () => {
    const { user } = openGitPage(() => {
      throw refused({ address: "skills/grill/logo.png is a binary file. A skill holds only text." })
    })

    await newSkill(user, "Add from git")
    await user.type(screen.getByRole("textbox", { name: "Address" }), "owner/repo")
    await user.click(screen.getByRole("button", { name: "Preview" }))

    const alert = await screen.findByRole("alert")
    expect(within(alert).getByText(/logo.png is a binary file/)).toBeTruthy()
  })
})

/** A page over a library the stub keeps, which writes and deletes change. */
function openEditablePage(write?: () => never) {
  let entries: LibraryEntry[] = [
    ...listed.entries,
    {
      kind: "skill",
      name: "pr",
      description: "Write the body.",
      source: "git",
      git: { url: "https://github.com/owner/repo.git", path: "skills/pr", ref: null, commit },
      edited: true,
    },
  ]
  const caller = stubCaller({
    "library.list": () => ({ entries }),
    "library.skill.read": () => ({ content: tdd, files: [], hash: "h-1" }),
    "library.skill.write": ({ name, content }) => {
      write?.()
      if (!entries.some((entry) => entry.name === name)) {
        entries = [...entries, { kind: "skill", name, description: "Ask why.", source: "written" }]
      }
      return { content, files: [], hash: "h-2" }
    },
    "library.skill.delete": ({ name }) => {
      entries = entries.filter((entry) => entry.name !== name)
      return { entries }
    },
  })
  return { caller, user: openLibrary(caller) }
}

const writes = (caller: ReturnType<typeof openEditablePage>["caller"]) =>
  caller.calls.filter((call) => /^library\.skill\.(write|delete)$/.test(call.method))

describe("LibraryPage, writing, editing and deleting a skill", () => {
  it("writes a new skill from its name, description and instructions", async () => {
    const { caller, user } = openEditablePage()

    await newSkill(user, "Write a skill")
    await user.type(screen.getByRole("textbox", { name: "Name" }), "grill")
    await user.type(screen.getByRole("textbox", { name: "Description" }), "Ask why.")
    await user.type(screen.getByRole("textbox", { name: "Instructions" }), "Ask why five times.")
    await user.click(screen.getByRole("button", { name: "Create" }))

    const yours = await screen.findByRole("list", { name: "Yours" })
    expect(await within(yours).findByRole("button", { name: "grill, written skill" })).toBeTruthy()
    expect(writes(caller)).toEqual([
      { method: "library.skill.write", params: { name: "grill", content: grill, hash: null } },
    ])
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull()
  })

  it("shows the hub's refusal of a name the library already holds", async () => {
    const { user } = openEditablePage(() => {
      throw refused({ name: 'The library already holds a skill "tdd".' })
    })

    await newSkill(user, "Write a skill")
    await user.type(screen.getByRole("textbox", { name: "Name" }), "tdd")
    await user.type(screen.getByRole("textbox", { name: "Description" }), "Mine.")
    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(await screen.findByText('The library already holds a skill "tdd".')).toBeTruthy()
  })

  it("marks an edited entry", async () => {
    openEditablePage()

    const yours = await screen.findByRole("list", { name: "Yours" })
    const pr = within(yours).getByRole("button", { name: "pr, git skill, edited" })
    expect(within(pr).getByText("Edited")).toBeTruthy()
    expect(screen.getAllByText("Edited")).toHaveLength(1)
  })

  it("saves an edit over the SKILL.md it read", async () => {
    const { caller, user } = openEditablePage()

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))
    const body = await screen.findByRole("textbox", { name: "SKILL.md" })
    await user.type(body, "Then refactor again.\n")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText(/Saved\./)).toBeTruthy()
    expect(writes(caller)).toEqual([
      {
        method: "library.skill.write",
        params: { name: "tdd", content: `${tdd}Then refactor again.\n`, hash: "h-1" },
      },
    ])
  })

  it("says when the skill changed since it was read", async () => {
    const { user } = openEditablePage(() => {
      throw new CallError({ code: "STALE", message: "Changed.", retryable: false })
    })

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))
    await user.type(await screen.findByRole("textbox", { name: "SKILL.md" }), "More.\n")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Changed since you opened it")).toBeTruthy()
  })

  it("deletes a skill after a one-line confirm", async () => {
    const { caller, user } = openEditablePage()

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))
    await screen.findByRole("textbox", { name: "SKILL.md" })
    await user.click(screen.getByRole("button", { name: "Delete" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Delete tdd?" })
    expect(within(dialog).getByText("Instances that picked it keep their copy.")).toBeTruthy()
    expect(writes(caller)).toEqual([])
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    const skills = await screen.findByRole("list", { name: "Shipped with kinby" })
    await waitFor(() =>
      expect(within(skills).queryByRole("button", { name: "tdd, shipped skill" })).toBeNull(),
    )
    expect(writes(caller)).toEqual([
      { method: "library.skill.delete", params: { name: "tdd", hash: "h-1" } },
    ])
    expect(screen.queryByRole("textbox", { name: "SKILL.md" })).toBeNull()
  })
})

const later = "fedcba9876543210fedcba9876543210fedcba98"
const diff = "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -4 +4 @@\n-Do it well.\n+Ask why five times.\n"

/** A page whose git skill pr and shipped skill unslop have newer versions. */
function openUpdatablePage({
  edited = false,
  move,
}: { edited?: boolean; move?: () => never } = {}) {
  const pr: LibraryEntry = {
    kind: "skill",
    name: "pr",
    description: "Write the body.",
    source: "git",
    git: { url: "https://github.com/owner/repo.git", path: "skills/pr", ref: null, commit },
    edited,
  }
  let entries: LibraryEntry[] = [
    listed.entries[2],
    { ...listed.entries[3], newer: true },
    pr,
  ].filter((entry) => entry !== undefined)
  const caller = stubCaller({
    "library.list": () => ({ entries }),
    "library.skill.read": () => ({ content: tdd, files: [], hash: "h-1" }),
    "library.skill.write": ({ content }) => ({ content, files: [], hash: "h-2" }),
    "library.skill.check": ({ name }) => ({
      newer:
        name === "tdd" ? null : { commit: name === "pr" ? later : null, hash: `h-${name}`, diff },
    }),
    "library.skill.move": ({ name }) => {
      move?.()
      entries = entries.map((entry) =>
        entry.name === name
          ? { ...entry, newer: false, edited: false, git: pr.git && { ...pr.git, commit: later } }
          : entry,
      )
      return { entries }
    },
  })
  return { caller, user: openLibrary(caller) }
}

const updates = (caller: ReturnType<typeof openUpdatablePage>["caller"]) =>
  caller.calls.filter((call) => /^library\.skill\.(check|move)$/.test(call.method))

describe("LibraryPage, moving a skill to a newer version", () => {
  it("marks a shipped skill kinby ships a newer version of", async () => {
    openUpdatablePage()

    expect(names(await screen.findByRole("list", { name: "Shipped with kinby" }))).toEqual([
      "tdd, shipped skill",
      "unslop, shipped skill, newer",
    ])
    expect(names(screen.getByRole("list", { name: "Yours" }))).toEqual(["pr, git skill"])
  })

  it("says a newer version is out on opening a skill the list marks, and reads its diff", async () => {
    const { caller, user } = openUpdatablePage()

    await user.click(await screen.findByRole("button", { name: "unslop, shipped skill, newer" }))
    expect(await screen.findByText("A newer version is out")).toBeTruthy()
    await user.click(screen.getByRole("button", { name: "Read the diff" }))

    expect(await screen.findByRole("textbox", { name: "Diff" })).toBeTruthy()
    expect(screen.getByText("The version this kinby ships.")).toBeTruthy()
    expect(updates(caller)).toEqual([{ method: "library.skill.check", params: { name: "unslop" } }])
  })

  it("checks a git skill, shows the diff, marks it and moves it to the commit it showed", async () => {
    const { caller, user } = openUpdatablePage()

    await user.click(await screen.findByRole("button", { name: "pr, git skill" }))
    await user.click(await screen.findByRole("button", { name: "Check for a newer version" }))

    const shown = await screen.findByRole("textbox", { name: "Diff" })
    expect((shown as HTMLTextAreaElement).value).toBe(diff)
    expect(screen.getByText(`Commit ${later}`)).toBeTruthy()
    expect(screen.getByRole("button", { name: "pr, git skill, newer" })).toBeTruthy()
    expect(updates(caller)).toEqual([{ method: "library.skill.check", params: { name: "pr" } }])

    await user.click(screen.getByRole("button", { name: "Move to this version" }))

    expect(await screen.findByRole("button", { name: "pr, git skill" })).toBeTruthy()
    expect(await screen.findByText(`https://github.com/owner/repo.git at ${later}`)).toBeTruthy()
    expect(screen.queryByRole("textbox", { name: "Diff" })).toBeNull()
    expect(updates(caller).at(-1)).toEqual({
      method: "library.skill.move",
      params: { name: "pr", hash: "h-pr", commit: later, overwrite: false },
    })
  })

  it("moves a shipped skill to the version kinby ships", async () => {
    const { caller, user } = openUpdatablePage()

    await user.click(await screen.findByRole("button", { name: "unslop, shipped skill, newer" }))
    await user.click(await screen.findByRole("button", { name: "Read the diff" }))
    await screen.findByRole("textbox", { name: "Diff" })
    await user.click(screen.getByRole("button", { name: "Move to this version" }))

    expect(await screen.findByRole("button", { name: "unslop, shipped skill" })).toBeTruthy()
    expect(updates(caller).at(-1)).toEqual({
      method: "library.skill.move",
      params: { name: "unslop", hash: "h-unslop", commit: null, overwrite: false },
    })
  })

  it("says a skill with no newer version is current", async () => {
    const { user } = openUpdatablePage()

    await user.click(await screen.findByRole("button", { name: "tdd, shipped skill" }))
    await user.click(await screen.findByRole("button", { name: "Check for a newer version" }))

    expect(await screen.findByText("No newer version.")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Move to this version" })).toBeNull()
  })

  it("asks before overwriting a skill the user edited", async () => {
    const { caller, user } = openUpdatablePage({ edited: true })

    await user.click(await screen.findByRole("button", { name: "pr, git skill, edited" }))
    await user.click(await screen.findByRole("button", { name: "Check for a newer version" }))
    await user.click(await screen.findByRole("button", { name: "Move to this version" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Overwrite your edits to pr?" })
    expect(updates(caller).some((call) => call.method === "library.skill.move")).toBe(false)
    await user.click(within(dialog).getByRole("button", { name: "Overwrite" }))

    expect(await screen.findByRole("button", { name: "pr, git skill" })).toBeTruthy()
    expect(updates(caller).at(-1)).toEqual({
      method: "library.skill.move",
      params: { name: "pr", hash: "h-pr", commit: later, overwrite: true },
    })
  })

  it("holds the move while SKILL.md has unsaved edits", async () => {
    const { caller, user } = openUpdatablePage()

    await user.click(await screen.findByRole("button", { name: "pr, git skill" }))
    await user.type(await screen.findByRole("textbox", { name: "SKILL.md" }), "More.\n")
    await user.click(screen.getByRole("button", { name: "Check for a newer version" }))
    await screen.findByRole("textbox", { name: "Diff" })

    const move = screen.getByRole("button", { name: "Move to this version" })
    expect(move.hasAttribute("disabled")).toBe(true)
    expect(screen.getByText("Save your edits to SKILL.md before moving it.")).toBeTruthy()
    await user.click(move)
    expect(caller.calls.some((call) => call.method === "library.skill.move")).toBe(false)
    expect((screen.getByRole("textbox", { name: "SKILL.md" }) as HTMLTextAreaElement).value).toBe(
      `${tdd}More.\n`,
    )
  })

  it("reads the diff again once the unsaved edits are saved", async () => {
    const { caller, user } = openUpdatablePage()

    await user.click(await screen.findByRole("button", { name: "pr, git skill" }))
    await user.type(await screen.findByRole("textbox", { name: "SKILL.md" }), "More.\n")
    await user.click(screen.getByRole("button", { name: "Check for a newer version" }))
    await screen.findByRole("textbox", { name: "Diff" })
    await user.click(screen.getByRole("button", { name: "Save" }))

    await screen.findByText(/Saved\./)
    expect(screen.queryByRole("textbox", { name: "Diff" })).toBeNull()
    await user.click(screen.getByRole("button", { name: "Read the diff" }))
    await screen.findByRole("textbox", { name: "Diff" })
    expect(updates(caller)).toEqual([
      { method: "library.skill.check", params: { name: "pr" } },
      { method: "library.skill.check", params: { name: "pr" } },
    ])
  })

  it("shows why the hub refused a move", async () => {
    const { user } = openUpdatablePage({
      move: () => {
        throw refused({ name: 'https://github.com/owner/repo.git holds no skill "pr".' })
      },
    })

    await user.click(await screen.findByRole("button", { name: "pr, git skill" }))
    await user.click(await screen.findByRole("button", { name: "Check for a newer version" }))
    await user.click(await screen.findByRole("button", { name: "Move to this version" }))

    const reason = await screen.findByText(/holds no skill "pr"/)
    expect(reason.closest("[role=alert]")?.textContent).toMatch(/^The hub refused it/)
  })

  it("offers no check for a written skill", async () => {
    const { user } = openEditablePage()

    await newSkill(user, "Write a skill")
    await user.type(screen.getByRole("textbox", { name: "Name" }), "grill")
    await user.type(screen.getByRole("textbox", { name: "Description" }), "Ask why.")
    await user.click(screen.getByRole("button", { name: "Create" }))
    await user.click(await screen.findByRole("button", { name: "grill, written skill" }))

    await screen.findByRole("textbox", { name: "SKILL.md" })
    expect(screen.queryByRole("button", { name: "Check for a newer version" })).toBeNull()
  })
})
