import { CallError, type Line } from "@kinby/contract"
import { type Answers, fakeClock } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { answers, hub, instances } from "@/components/factories-testing"
import { LinePage } from "@/components/line-page"

/** The kinby line as the fixture holds it. */
const kinby = answers["line.get"]?.({ line_id: "l-kinby" }) as Line

/** The line's page, on a hub that holds the line as each change leaves it. */
function openLine(more: Answers = {}) {
  let held = kinby
  const caller = hub({
    "line.get": () => held,
    "line.update": ({ model = null, usd_per_day = null }) => {
      held = { ...held, model, usd_per_day }
      return held
    },
    "line.owner.set": ({ owner_id }) => {
      held = { ...held, owner_id }
      return held
    },
    "line.remove": () => ({ lines: [] }),
    ...more,
  })
  render(<LinePage caller={caller} clock={fakeClock()} lineId="l-kinby" instances={instances} />)
  return caller
}

/** The line's detail by its term, as the page lists them. */
const detail = (term: string) => screen.getByText(term, { selector: "dt" }).nextElementSibling

const dialog = () => within(screen.getByRole("dialog"))

async function choose(user: ReturnType<typeof userEvent.setup>, action: string) {
  await user.click(await screen.findByRole("button", { name: "More actions" }))
  await user.click(await screen.findByRole("menuitem", { name: action }))
}

describe("changing a line", () => {
  beforeEach(() => window.history.replaceState(null, "", "/factories/lines/l-kinby"))

  it("moves the line to another owner", async () => {
    const user = userEvent.setup()
    const caller = openLine()

    await choose(user, "Change owner…")
    expect(dialog().getByText(/the memory marked kinby move to the new owner/)).toBeDefined()
    await user.click(dialog().getByRole("combobox", { name: "New owner" }))
    await user.click(await screen.findByRole("option", { name: "Finance" }))
    await user.click(dialog().getByRole("button", { name: "Move" }))

    await waitFor(() => expect(detail("Owner")?.textContent).toBe("FFinance"))
    expect(caller.calls.find(({ method }) => method === "line.owner.set")?.params).toEqual({
      line_id: "l-kinby",
      owner_id: "finance",
    })
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("changes the line's model, keeping its budget", async () => {
    const user = userEvent.setup()
    const caller = openLine()

    await choose(user, "Change model…")
    const model = dialog().getByLabelText("Model")
    await user.clear(model)
    await user.type(model, "claude-sonnet-5")
    await user.click(dialog().getByRole("button", { name: "Save" }))

    await waitFor(() => expect(detail("Model")?.textContent).toBe("claude-sonnet-5"))
    expect(caller.calls.find(({ method }) => method === "line.update")?.params).toEqual({
      line_id: "l-kinby",
      model: "claude-sonnet-5",
      usd_per_day: 20,
    })
  })

  it("changes the line's daily budget, and an empty one is none", async () => {
    const user = userEvent.setup()
    const caller = openLine()

    await choose(user, "Change daily budget…")
    await user.clear(dialog().getByLabelText("Daily budget in USD"))
    await user.click(dialog().getByRole("button", { name: "Save" }))

    await waitFor(() => expect(detail("Daily budget")?.textContent).toBe("None"))
    expect(caller.calls.find(({ method }) => method === "line.update")?.params).toEqual({
      line_id: "l-kinby",
      model: "claude-opus-5-5",
      usd_per_day: null,
    })

    await choose(user, "Change daily budget…")
    await user.type(dialog().getByLabelText("Daily budget in USD"), "7.5")
    await user.click(dialog().getByRole("button", { name: "Save" }))
    await waitFor(() => expect(detail("Daily budget")?.textContent).toBe("$7.50 a day"))
  })

  it("says why the hub refused a change, and keeps the dialog open", async () => {
    const user = userEvent.setup()
    openLine({
      "line.owner.set": () => {
        throw new CallError({
          code: "INVALID_ARGUMENT",
          message: "A run of the line is running a step.",
          retryable: false,
        })
      },
    })

    await choose(user, "Change owner…")
    await user.click(dialog().getByRole("combobox", { name: "New owner" }))
    await user.click(await screen.findByRole("option", { name: "Finance" }))
    await user.click(dialog().getByRole("button", { name: "Move" }))

    expect((await dialog().findByRole("alert")).textContent).toContain(
      "A run of the line is running a step.",
    )
  })

  it("removes the line and goes back to the Factories area", async () => {
    const user = userEvent.setup()
    const caller = openLine()

    await choose(user, "Remove line")
    await user.click(screen.getByRole("button", { name: "Remove" }))

    await waitFor(() => expect(window.location.pathname).toBe("/factories"))
    expect(caller.calls.find(({ method }) => method === "line.remove")?.params).toEqual({
      line_id: "l-kinby",
    })
  })
})
