import { CallError } from "@kinby/contract"
import type { ConfigChange, RoutineSummary } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { RoutinesSection } from "@/components/routines-section"

const NEWS = "---\ndescription: Morning news\nschedule: 0 9 * * *\n---\nRead the news.\n"

// Local times, so the page shows the same clock times wherever the test runs.
const news: RoutineSummary = {
  name: "news",
  description: "Morning news",
  schedule: "0 9 * * *",
  enabled: true,
  mode: "auto",
  next_run: new Date(2026, 8, 29, 9, 0).toISOString(),
  last_run: {
    started_at: new Date(2026, 8, 28, 9, 0).toISOString(),
    outcome: "work",
    thread_id: "thread-1",
    turn_id: "turn-1",
  },
  failure_count: 0,
  pending: 0,
}

const issues: RoutineSummary = {
  name: "issues",
  description: "Triage new issues",
  schedule: null,
  enabled: false,
  mode: "ask",
  next_run: null,
  last_run: {
    started_at: new Date(2026, 8, 28, 11, 0).toISOString(),
    outcome: "parked",
    thread_id: "thread-4",
    turn_id: "turn-4",
  },
  failure_count: 10,
  pending: 2,
  signal: { path: "/signals/issues", auth: "hmac-sha256" },
}

const disabledByThePolicy: ConfigChange = {
  at: new Date(2026, 8, 28, 10, 4).toISOString(),
  file: "routines/issues",
  actor: "failure_policy",
  thread_id: "thread-2",
  turn_id: "turn-2",
  diff: "",
  hash: "hash-issues",
}

function openSection(answers: Answers = {}, opened?: string) {
  const caller = stubCaller({
    "routine.list": () => ({ routines: [issues, news], warnings: [] }),
    "routine.read": ({ name }) => ({ name, content: NEWS, hash: `hash-${name}` }),
    "config.history": ({ file }) => ({
      changes: file === "routines/issues" ? [disabledByThePolicy] : [],
    }),
    "routine.grant.list": () => ({ grants: [] }),
    ...answers,
  })
  render(<RoutinesSection client={caller} clock={fakeClock()} opened={opened} />)
  return { caller, user: userEvent.setup() }
}

/** Unfold the routine's row with its chevron, and return the row. */
async function unfold(user: ReturnType<typeof userEvent.setup>, name: string) {
  const row = await screen.findByRole("listitem", { name })
  await user.click(within(row).getByRole("button", { name: "Details" }))
  return row
}

describe("RoutinesSection", () => {
  it("shows each routine as a row: its trigger in words, next and last, its badges and its switch", async () => {
    openSection()

    const newsRow = await screen.findByRole("listitem", { name: "news" })
    const issuesRow = screen.getByRole("listitem", { name: "issues" })

    expect(within(newsRow).getByRole("switch", { name: "Enabled" }).ariaChecked).toBe("true")
    expect(
      within(newsRow).getByText(
        "Daily at 09:00 · Next firing Sep 29, 2026, 9:00 AM · Last firing Sep 28, 2026, 9:00 AM, did work",
      ),
    ).toBeDefined()
    expect(within(newsRow).queryByText(/failure|pending|Waiting/)).toBeNull()

    expect(within(issuesRow).getByRole("switch", { name: "Enabled" }).ariaChecked).toBe("false")
    expect(
      within(issuesRow).getByText(
        "When a signal arrives at /signals/issues · No next firing · Last firing Sep 28, 2026, 11:00 AM, waiting on an approval",
      ),
    ).toBeDefined()
    expect(within(issuesRow).getByText("10 failures in a row")).toBeDefined()
    expect(within(issuesRow).getByText("2 pending deliveries")).toBeDefined()
    expect(within(issuesRow).getByText("Waiting on an approval")).toBeDefined()
    expect(screen.queryByRole("textbox")).toBeNull()
  })

  it("unfolds one routine at a time to its details and its editor", async () => {
    const { user } = openSection()

    const newsRow = await unfold(user, "news")
    expect(within(newsRow).getByText("Morning news")).toBeDefined()
    expect(within(newsRow).getByText("Schedule 0 9 * * *")).toBeDefined()
    expect(within(newsRow).getByText("auto")).toBeDefined()
    expect(within(newsRow).getByText("Never changed")).toBeDefined()
    expect(await within(newsRow).findByRole("textbox", { name: "ROUTINE.md" })).toHaveProperty(
      "value",
      NEWS,
    )

    const issuesRow = await unfold(user, "issues")
    expect(within(issuesRow).getByText("Signal at /signals/issues")).toBeDefined()
    expect(within(issuesRow).getByText("ask")).toBeDefined()
    expect(
      within(issuesRow).getByText(
        "Last changed by the routine failure policy, Sep 28, 2026, 10:04 AM",
      ),
    ).toBeDefined()
    expect(await within(issuesRow).findByRole("textbox", { name: "ROUTINE.md" })).toBeDefined()
    await waitFor(() => expect(within(newsRow).queryByRole("textbox")).toBeNull())
  })

  it("unfolds the routine a link names", async () => {
    openSection({}, "issues")

    const row = await screen.findByRole("listitem", { name: "issues" })

    expect(within(row).getByRole("button", { name: "Details" }).ariaExpanded).toBe("true")
    expect(await within(row).findByRole("textbox", { name: "ROUTINE.md" })).toBeDefined()
  })

  it("opens the editor of a linked routine the list leaves out, so it can be repaired", async () => {
    let routines = [issues, news]
    const { caller, user } = openSection(
      {
        "routine.list": () => ({ routines, warnings: [] }),
        "routine.write": ({ name, content }) => {
          routines = [...routines, { ...news, name }]
          return { name, content, hash: "hash-fixed" }
        },
      },
      "broken",
    )

    const editor = await screen.findByRole("region", { name: "broken" })
    const field = await within(editor).findByRole("textbox", { name: "ROUTINE.md" })
    expect(field).toHaveProperty("value", NEWS)
    await user.type(field, "Fixed.")
    await user.click(within(editor).getByRole("button", { name: "Save" }))

    const row = await screen.findByRole("listitem", { name: "broken" })
    expect(within(row).getByRole("button", { name: "Details" }).ariaExpanded).toBe("true")
    expect(screen.queryByRole("region", { name: "broken" })).toBeNull()
    expect(caller.calls.filter((call) => call.method === "routine.write").at(-1)?.params).toEqual({
      name: "broken",
      content: `${NEWS}Fixed.`,
      hash: "hash-broken",
    })
  })

  it("says a routine changed outside kinby when its directory differs from the last change", async () => {
    const { user } = openSection({
      "routine.read": ({ name }) => ({ name, content: NEWS, hash: "hash-by-hand" }),
    })

    const issuesRow = await unfold(user, "issues")

    expect(within(issuesRow).getByText("Changed outside kinby")).toBeDefined()
    expect(within(issuesRow).queryByText(/Last changed by/)).toBeNull()
  })

  it("leaves out a routine removed between the list and its read", async () => {
    openSection({
      "routine.read": ({ name }) => {
        if (name === "issues") {
          throw new CallError({ code: "NOT_FOUND", message: "Gone.", retryable: false })
        }
        return { name, content: NEWS, hash: `hash-${name}` }
      },
    })

    await screen.findByRole("listitem", { name: "news" })
    expect(screen.queryByRole("listitem", { name: "issues" })).toBeNull()
  })

  it("lists the tools a routine always allows, and revokes one", async () => {
    let grants = [
      {
        routine: "news",
        tool: "post",
        granted_at: new Date(2026, 8, 28, 9, 5).toISOString(),
        approval_id: "a1",
      },
    ]
    const { caller, user } = openSection({
      "routine.grant.list": ({ name }) => ({ grants: name === "news" ? grants : [] }),
      "routine.grant.revoke": ({ tool }) => {
        grants = grants.filter((grant) => grant.tool !== tool)
        return { grants }
      },
    })

    const issuesRow = await unfold(user, "issues")
    await within(issuesRow).findByRole("textbox", { name: "ROUTINE.md" })
    expect(within(issuesRow).queryByRole("list", { name: "Always allowed" })).toBeNull()
    const item = await unfold(user, "news")
    const allowed = await within(item).findByRole("list", { name: "Always allowed" })
    expect(allowed.textContent).toContain("post, since Sep 28, 2026, 9:05 AM")

    await user.click(within(allowed).getByRole("button", { name: "Revoke post" }))

    await waitFor(() =>
      expect(within(item).queryByRole("list", { name: "Always allowed" })).toBeNull(),
    )
    expect(caller.calls.filter((call) => call.method === "routine.grant.revoke")).toEqual([
      { method: "routine.grant.revoke", params: { name: "news", tool: "post" } },
    ])
  })

  it("turns a routine off with its switch and shows it off", async () => {
    let enabled = true
    const { caller, user } = openSection({
      "routine.list": () => ({ routines: [{ ...news, enabled }], warnings: [] }),
      "routine.set_enabled": (params) => {
        enabled = params.enabled
        return { name: params.name, content: "", hash: "hash-2" }
      },
    })

    const item = await screen.findByRole("listitem", { name: "news" })
    await user.click(within(item).getByRole("switch", { name: "Enabled" }))

    await waitFor(() =>
      expect(within(item).getByRole("switch", { name: "Enabled" }).ariaChecked).toBe("false"),
    )
    expect(caller.calls.filter((call) => call.method === "routine.set_enabled")).toEqual([
      { method: "routine.set_enabled", params: { name: "news", enabled: false } },
    ])
  })

  it("runs a routine now", async () => {
    const { caller, user } = openSection({
      "routine.run": () => ({ sequence: 1, thread_id: "thread-3", turn_id: "turn-3" }),
    })

    const item = await unfold(user, "news")
    await user.click(within(item).getByRole("button", { name: "Run now" }))

    expect(await screen.findByText("Started news. Its turn is in a new thread.")).toBeDefined()
    expect(caller.calls.filter((call) => call.method === "routine.run")).toEqual([
      { method: "routine.run", params: { name: "news" } },
    ])
  })

  it("says the instance refused a run while a user turn runs", async () => {
    const { user } = openSection({
      "routine.run": () => {
        throw new CallError({
          code: "INSTANCE_BUSY",
          message: "A user turn is running.",
          retryable: true,
        })
      },
    })

    const item = await unfold(user, "news")
    await user.click(within(item).getByRole("button", { name: "Run now" }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("The instance refused it")
    expect(alert.textContent).toContain("A user turn is running.")
  })

  it("edits a routine over the hash it read, and offers to load theirs when it changed since", async () => {
    let theirs = false
    const { caller, user } = openSection({
      "routine.read": ({ name }) =>
        theirs
          ? { name, content: NEWS.replace("Read", "Skim"), hash: "hash-3" }
          : { name, content: NEWS, hash: "hash-1" },
      "routine.write": ({ name, content }) => {
        if (!theirs) {
          theirs = true
          throw new CallError({
            code: "STALE",
            message: "routines/news changed.",
            retryable: false,
          })
        }
        return { name, content, hash: "hash-4" }
      },
    })

    const item = await unfold(user, "news")
    const editor = await within(item).findByRole("textbox", { name: "ROUTINE.md" })
    expect(editor).toHaveProperty("value", NEWS)
    await user.type(editor, "Mine.")
    await user.click(within(item).getByRole("button", { name: "Save" }))
    const alert = await within(item).findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))
    await waitFor(() => expect(editor).toHaveProperty("value", NEWS.replace("Read", "Skim")))
    await user.type(editor, "Mine.")
    const lists = caller.calls.filter((call) => call.method === "routine.list").length
    await user.click(within(item).getByRole("button", { name: "Save" }))

    expect(await within(item).findByText("Saved.")).toBeDefined()
    expect(
      caller.calls.filter((call) => call.method === "routine.write").map((call) => call.params),
    ).toEqual([
      { name: "news", content: `${NEWS}Mine.`, hash: "hash-1" },
      { name: "news", content: `${NEWS.replace("Read", "Skim")}Mine.`, hash: "hash-3" },
    ])
    // The row reads its new hash and last change again.
    await waitFor(() =>
      expect(caller.calls.filter((call) => call.method === "routine.list").length).toBe(lists + 1),
    )
  })

  it("creates a routine at the top of the list with a null hash, and shows the loader's refusal", async () => {
    let routines = [issues, news]
    const { caller, user } = openSection({
      "routine.list": () => ({ routines, warnings: [] }),
      "routine.write": ({ name, content }) => {
        if (!content.includes("description: Weather")) {
          throw new CallError({
            code: "INVALID_ARGUMENT",
            message: 'Frontmatter must contain "description".',
            retryable: false,
          })
        }
        routines = [...routines, { ...news, name: "weather", description: "Weather" }]
        return { name, content, hash: "hash-5" }
      },
    })

    await screen.findByRole("listitem", { name: "news" })
    await user.click(screen.getByRole("button", { name: "New routine" }))
    const form = screen.getByRole("region", { name: "New routine" })
    expect(
      form.compareDocumentPosition(screen.getByRole("list", { name: "Routines" })) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    const name = within(form).getByRole("textbox", { name: "Name" })
    expect(document.activeElement).toBe(name)
    await user.type(name, "weather")
    const editor = within(form).getByRole("textbox", { name: "ROUTINE.md" })
    expect(editor).toHaveProperty("value", "---\ndescription: \nschedule: 0 9 * * *\n---\n")
    await user.click(within(form).getByRole("button", { name: "Save" }))
    expect((await within(form).findByRole("alert")).textContent).toContain(
      'Frontmatter must contain "description".',
    )
    await user.clear(editor)
    await user.type(editor, "---\ndescription: Weather\n---\nCheck the sky.")
    await user.click(within(form).getByRole("button", { name: "Save" }))

    const row = await screen.findByRole("listitem", { name: "weather" })
    expect(within(row).getByRole("button", { name: "Details" }).ariaExpanded).toBe("true")
    expect(screen.queryByRole("region", { name: "New routine" })).toBeNull()
    expect(caller.calls.filter((call) => call.method === "routine.write").at(-1)?.params).toEqual({
      name: "weather",
      content: "---\ndescription: Weather\n---\nCheck the sky.",
      hash: null,
    })
  })

  it("renames a routine, naming its old and new signal paths, and lists it under the new name", async () => {
    let routines = [issues, news]
    const { caller, user } = openSection({
      "routine.list": () => ({ routines, warnings: [] }),
      "routine.rename": ({ name, new_name }) => {
        routines = routines.map((routine) =>
          routine.name === name
            ? {
                ...routine,
                name: new_name,
                signal: { path: `/signals/${new_name}`, auth: "hmac-sha256" },
              }
            : routine,
        )
        return { name: new_name, content: NEWS, hash: `hash-${new_name}` }
      },
    })

    const item = await unfold(user, "issues")
    await user.click(within(item).getByRole("button", { name: "Rename" }))
    const dialog = await screen.findByRole("dialog", { name: "Rename issues" })
    await user.type(within(dialog).getByRole("textbox", { name: "New name" }), "triage")
    expect(
      within(dialog).getByText(
        "Its webhook path changes from /signals/issues to /signals/triage. The old path stops answering.",
      ),
    ).toBeDefined()
    await user.click(within(dialog).getByRole("button", { name: "Rename" }))

    const renamed = await screen.findByRole("listitem", { name: "triage" })
    expect(within(renamed).getByRole("button", { name: "Details" }).ariaExpanded).toBe("true")
    expect(screen.queryByRole("listitem", { name: "issues" })).toBeNull()
    expect(screen.queryByRole("dialog")).toBeNull()
    expect(
      caller.calls.filter((call) => call.method === "routine.rename").map((call) => call.params),
    ).toEqual([{ name: "issues", new_name: "triage", hash: "hash-issues" }])
  })

  it("says nothing of a signal path when renaming a routine without a signal", async () => {
    const { user } = openSection()

    const item = await unfold(user, "news")
    await user.click(within(item).getByRole("button", { name: "Rename" }))
    const dialog = await screen.findByRole("dialog", { name: "Rename news" })

    expect(within(dialog).queryByText(/signals/)).toBeNull()
  })

  it("shows each refusal in the rename dialog, and renames over the hash read again", async () => {
    let read = 1
    const refusals = [
      new CallError({
        code: "INVALID_ARGUMENT",
        message: "Some config values are invalid.",
        retryable: false,
        fields: { new_name: 'Routine "news" already exists.' },
      }),
      new CallError({
        code: "ROUTINE_PENDING",
        message: 'Routine "issues" has 2 pending deliveries and cannot be renamed.',
        retryable: false,
      }),
      new CallError({
        code: "STALE",
        message: "routines/issues changed since it was read. Read it again.",
        retryable: false,
      }),
    ]
    const { caller, user } = openSection({
      "routine.read": ({ name }) => ({ name, content: NEWS, hash: `hash-${name}-${read}` }),
      "routine.rename": ({ new_name }) => {
        const refusal = refusals.shift()
        if (refusal === undefined) return { name: new_name, content: NEWS, hash: "hash-triage" }
        if (refusal.code === "STALE") read = 2
        throw refusal
      },
    })

    const item = await unfold(user, "issues")
    await user.click(within(item).getByRole("button", { name: "Rename" }))
    const dialog = await screen.findByRole("dialog", { name: "Rename issues" })
    const field = within(dialog).getByRole("textbox", { name: "New name" })
    const submit = within(dialog).getByRole("button", { name: "Rename" })
    await user.type(field, "news")
    await user.click(submit)
    expect(await within(dialog).findByText('Routine "news" already exists.')).toBeDefined()
    expect(field.getAttribute("aria-invalid")).toBe("true")
    await user.clear(field)
    await user.type(field, "triage")
    await user.click(submit)
    expect(
      await within(dialog).findByText(
        'Routine "issues" has 2 pending deliveries and cannot be renamed.',
      ),
    ).toBeDefined()
    await user.click(submit)
    expect(
      await within(dialog).findByText("routines/issues changed since it was read. Read it again."),
    ).toBeDefined()
    await user.click(submit)

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(
      caller.calls.filter((call) => call.method === "routine.rename").map((call) => call.params),
    ).toEqual([
      { name: "issues", new_name: "news", hash: "hash-issues-1" },
      { name: "issues", new_name: "triage", hash: "hash-issues-1" },
      { name: "issues", new_name: "triage", hash: "hash-issues-1" },
      { name: "issues", new_name: "triage", hash: "hash-issues-2" },
    ])
  })

  it("keeps Rename off after a stale refusal until the list has read the new hash", async () => {
    let read = 1
    const stub = stubCaller({
      "routine.list": () => ({ routines: [issues, news], warnings: [] }),
      "routine.read": ({ name }) => ({ name, content: NEWS, hash: `hash-${name}-${read}` }),
      "config.history": () => ({ changes: [] }),
      "routine.rename": ({ new_name, hash }) => {
        if (hash === "hash-issues-2") return { name: new_name, content: NEWS, hash: "hash-triage" }
        read = 2
        throw new CallError({ code: "STALE", message: "Read it again.", retryable: false })
      },
    })
    // Holds the list's reads until the test lets them through.
    let lists = 0
    let gate = Promise.resolve()
    const caller: typeof stub = {
      calls: stub.calls,
      call: async (method, params) => {
        if (method === "routine.list") {
          lists += 1
          await gate
        }
        return stub.call(method, params)
      },
    }
    render(<RoutinesSection client={caller} clock={fakeClock()} />)
    const user = userEvent.setup()

    const item = await unfold(user, "issues")
    await user.click(within(item).getByRole("button", { name: "Rename" }))
    const dialog = await screen.findByRole("dialog", { name: "Rename issues" })
    await user.type(within(dialog).getByRole("textbox", { name: "New name" }), "triage")
    const submit = within(dialog).getByRole<HTMLButtonElement>("button", { name: "Rename" })
    let release = () => {}
    gate = new Promise((resolve) => (release = resolve))
    await user.click(submit)
    await waitFor(() => expect(lists).toBe(2))
    await act(async () => {})
    expect(submit.disabled).toBe(true)

    release()
    expect(await within(dialog).findByText("Read it again.")).toBeDefined()
    await waitFor(() => expect(submit.disabled).toBe(false))
    await user.click(submit)

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(
      stub.calls.filter((call) => call.method === "routine.rename").map((call) => call.params),
    ).toEqual([
      { name: "issues", new_name: "triage", hash: "hash-issues-1" },
      { name: "issues", new_name: "triage", hash: "hash-issues-2" },
    ])
  })

  it("deletes a routine once confirmed, and shows why when deliveries are pending", async () => {
    let routines = [issues, news]
    const { caller, user } = openSection({
      "routine.list": () => ({ routines, warnings: [] }),
      "routine.read": ({ name }) => ({ name, content: NEWS, hash: `hash-${name}` }),
      "routine.delete": ({ name }) => {
        if (name === "issues") {
          throw new CallError({
            code: "ROUTINE_PENDING",
            message: 'Routine "issues" has 2 pending deliveries and cannot be deleted.',
            retryable: false,
          })
        }
        routines = routines.filter((routine) => routine.name !== name)
        return {}
      },
    })
    const remove = async (name: string) => {
      const item = await unfold(user, name)
      await within(item).findByRole("textbox", { name: "ROUTINE.md" })
      await user.click(within(item).getByRole("button", { name: "Delete" }))
      const dialog = await screen.findByRole("alertdialog", { name: `Delete ${name}?` })
      await user.click(within(dialog).getByRole("button", { name: "Delete" }))
      return item
    }

    const issuesRow = await remove("issues")
    expect((await within(issuesRow).findByRole("alert")).textContent).toContain(
      'Routine "issues" has 2 pending deliveries and cannot be deleted.',
    )
    await remove("news")

    await waitFor(() => expect(screen.queryByRole("listitem", { name: "news" })).toBeNull())
    expect(screen.getByRole("listitem", { name: "issues" })).toBeDefined()
    expect(
      caller.calls.filter((call) => call.method === "routine.delete").map((call) => call.params),
    ).toEqual([
      { name: "issues", hash: "hash-issues" },
      { name: "news", hash: "hash-news" },
    ])
  })
})
