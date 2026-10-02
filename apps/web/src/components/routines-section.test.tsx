import { CallError } from "@kinby/contract"
import type { ConfigChange, RoutineSummary } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
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
  last_run: null,
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

function openSection(answers: Answers = {}) {
  const caller = stubCaller({
    "routine.list": () => ({ routines: [issues, news], warnings: [] }),
    "routine.read": ({ name }) => ({ name, content: NEWS, hash: `hash-${name}` }),
    "config.history": ({ file }) => ({
      changes: file === "routines/issues" ? [disabledByThePolicy] : [],
    }),
    ...answers,
  })
  render(<RoutinesSection client={caller} clock={fakeClock()} />)
  return { caller, user: userEvent.setup() }
}

describe("RoutinesSection", () => {
  it("shows no list marker beside a routine", async () => {
    openSection()

    await screen.findByRole("listitem", { name: "news" })
    // jsdom applies no Tailwind: an `Item` rendered as the `<li>` lays out as flex, so no marker.
    const items = within(screen.getByRole("list", { name: "Routines" })).getAllByRole("listitem")
    expect(items.map((item) => item.classList.contains("flex"))).toEqual([true, true])
  })

  it("lists each routine with its firing, state, failures, pending deliveries, and last change", async () => {
    openSection()

    const newsItem = await screen.findByRole("listitem", { name: "news" })
    const issuesItem = screen.getByRole("listitem", { name: "issues" })

    expect(within(newsItem).getByRole("switch", { name: "On" }).ariaChecked).toBe("true")
    expect(within(newsItem).getByText("Morning news")).toBeDefined()
    expect(within(newsItem).getByText("Schedule 0 9 * * *")).toBeDefined()
    expect(within(newsItem).getByText("Next firing Sep 29, 2026, 9:00 AM")).toBeDefined()
    expect(within(newsItem).getByText("Last firing Sep 28, 2026, 9:00 AM, did work")).toBeDefined()
    expect(within(newsItem).getByText("Never changed")).toBeDefined()
    expect(within(newsItem).queryByText(/failure/)).toBeNull()
    expect(within(newsItem).queryByText(/pending/)).toBeNull()

    expect(within(issuesItem).getByRole("switch", { name: "On" }).ariaChecked).toBe("false")
    expect(within(issuesItem).getByText("Signal at /signals/issues")).toBeDefined()
    expect(within(issuesItem).getByText("No next firing")).toBeDefined()
    expect(within(issuesItem).getByText("Never fired")).toBeDefined()
    expect(within(issuesItem).getByText("10 failures in a row")).toBeDefined()
    expect(within(issuesItem).getByText("2 pending deliveries")).toBeDefined()
    expect(
      within(issuesItem).getByText(
        "Last changed by the routine failure policy, Sep 28, 2026, 10:04 AM",
      ),
    ).toBeDefined()
  })

  it("says a routine changed outside kinby when its directory differs from the last change", async () => {
    openSection({
      "routine.read": ({ name }) => ({ name, content: NEWS, hash: "hash-by-hand" }),
    })

    const issuesItem = await screen.findByRole("listitem", { name: "issues" })

    expect(within(issuesItem).getByText("Changed outside kinby")).toBeDefined()
    expect(within(issuesItem).queryByText(/Last changed by/)).toBeNull()
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
    await user.click(within(item).getByRole("switch", { name: "On" }))

    await waitFor(() =>
      expect(within(item).getByRole("switch", { name: "On" }).ariaChecked).toBe("false"),
    )
    expect(caller.calls.filter((call) => call.method === "routine.set_enabled")).toEqual([
      { method: "routine.set_enabled", params: { name: "news", enabled: false } },
    ])
  })

  it("runs a routine now", async () => {
    const { caller, user } = openSection({
      "routine.run": () => ({ sequence: 1, thread_id: "thread-3", turn_id: "turn-3" }),
    })

    const item = await screen.findByRole("listitem", { name: "news" })
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

    const item = await screen.findByRole("listitem", { name: "news" })
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

    const item = await screen.findByRole("listitem", { name: "news" })
    await user.click(within(item).getByRole("button", { name: "Edit" }))
    const editor = await screen.findByRole("textbox", { name: "ROUTINE.md" })
    expect(editor).toHaveProperty("value", NEWS)
    await user.type(editor, "Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))
    await waitFor(() => expect(editor).toHaveProperty("value", NEWS.replace("Read", "Skim")))
    await user.type(editor, "Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Saved.")).toBeDefined()
    expect(
      caller.calls.filter((call) => call.method === "routine.write").map((call) => call.params),
    ).toEqual([
      { name: "news", content: `${NEWS}Mine.`, hash: "hash-1" },
      { name: "news", content: `${NEWS.replace("Read", "Skim")}Mine.`, hash: "hash-3" },
    ])
    await user.click(screen.getByRole("button", { name: "Back to routines" }))
    expect(await screen.findByRole("listitem", { name: "news" })).toBeDefined()
  })

  it("creates a routine with a null hash and shows the loader's refusal", async () => {
    const { caller, user } = openSection({
      "routine.write": ({ name, content }) => {
        if (!content.includes("description: Weather")) {
          throw new CallError({
            code: "INVALID_ARGUMENT",
            message: 'Frontmatter must contain "description".',
            retryable: false,
          })
        }
        return { name, content, hash: "hash-5" }
      },
    })

    await user.click(await screen.findByRole("button", { name: "New routine" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "weather")
    const editor = screen.getByRole("textbox", { name: "ROUTINE.md" })
    expect(editor).toHaveProperty("value", "---\ndescription: \nschedule: 0 9 * * *\n---\n")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect((await screen.findByRole("alert")).textContent).toContain(
      'Frontmatter must contain "description".',
    )
    await user.clear(editor)
    await user.type(editor, "---\ndescription: Weather\n---\nCheck the sky.")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Saved.")).toBeDefined()
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull()
    expect(caller.calls.filter((call) => call.method === "routine.write").at(-1)?.params).toEqual({
      name: "weather",
      content: "---\ndescription: Weather\n---\nCheck the sky.",
      hash: null,
    })
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
      const item = await screen.findByRole("listitem", { name })
      await user.click(within(item).getByRole("button", { name: "Edit" }))
      await screen.findByRole("textbox", { name: "ROUTINE.md" })
      await user.click(screen.getByRole("button", { name: "Delete" }))
      const dialog = await screen.findByRole("alertdialog", { name: `Delete ${name}?` })
      await user.click(within(dialog).getByRole("button", { name: "Delete" }))
    }

    await remove("issues")
    expect((await screen.findByRole("alert")).textContent).toContain(
      'Routine "issues" has 2 pending deliveries and cannot be deleted.',
    )
    await user.click(screen.getByRole("button", { name: "Back to routines" }))
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
