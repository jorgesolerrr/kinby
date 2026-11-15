import { fakeClock } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { FactoriesPage } from "@/components/factories-page"
import { hub, instances } from "@/components/factories-testing"
import { rows } from "@/components/stats-testing"

function openArea() {
  render(<FactoriesPage caller={hub()} clock={fakeClock()} instances={instances} />)
}

/** The work items on the board's column, top to bottom. */
const column = (name: string) =>
  within(screen.getByRole("region", { name }))
    .queryAllByRole("link")
    .map((card) => card.getAttribute("aria-label"))

describe("the Factories area", () => {
  beforeEach(() => window.history.replaceState(null, "", "/factories"))

  it("lists each factory with its steps and a table of its lines", async () => {
    openArea()

    const software = within(await screen.findByRole("region", { name: "software" }))
    expect(software.getByText("ticket → implement → publish")).toBeDefined()
    expect(rows(software.getByRole("table"))).toEqual([
      ["kinby", "KKinby", "Needs a humanRunning", "$20.00 a day"],
      ["landing", "FFinance", "Needs youQueued", "None"],
    ])
    const course = within(screen.getByRole("region", { name: "course" }))
    expect(rows(course.getByRole("table"))).toEqual([
      ["cec-moodle", "KKinby", "Needs youParked", "None"],
      ["kinby", "KKinby", "None", "None"],
    ])
  })

  it("says a factory written the older way runs in the instances that hold its roles", async () => {
    openArea()

    const older = within(await screen.findByRole("region", { name: "reading-brief" }))
    expect(older.queryByRole("table")).toBeNull()
    expect(older.getByText(/Its steps run in the instances that hold its roles/)).toBeDefined()
  })

  it("shows the unfinished runs on a board: those that need you, running, and waiting", async () => {
    openArea()

    await screen.findByRole("region", { name: "software" })
    expect(column("Needs you")).toEqual([
      "issue 1305 on software on kinby",
      "issue 40 on software on landing",
      "unit 3 on course on cec-moodle",
    ])
    expect(column("Running")).toEqual(["issue 1310 on software on kinby"])
    expect(column("Waiting")).toEqual([
      "issue 41 on software on landing",
      "unit 4 on course on cec-moodle",
    ])
    const stopped = within(screen.getByRole("link", { name: "issue 1305 on software on kinby" }))
    expect(stopped.getByText("Needs a human").dataset.variant).toBe("attention")
    expect(stopped.getByText("implement")).toBeDefined()
  })

  it("narrows the board and the factories to one factory", async () => {
    const user = userEvent.setup()
    openArea()

    await screen.findByRole("region", { name: "software" })
    await user.click(screen.getByRole("button", { name: "course" }))

    expect(screen.getByRole("button", { name: "course" }).getAttribute("aria-pressed")).toBe("true")
    expect(column("Needs you")).toEqual(["unit 3 on course on cec-moodle"])
    expect(column("Running")).toEqual([])
    expect(screen.queryByRole("region", { name: "software" })).toBeNull()
    expect(screen.getByRole("region", { name: "course" })).toBeDefined()

    await user.click(screen.getByRole("button", { name: "All" }))
    expect(screen.getByRole("region", { name: "software" })).toBeDefined()
  })

  it("opens a line's page from its row and from a run's card", async () => {
    const user = userEvent.setup()
    openArea()

    const software = within(await screen.findByRole("region", { name: "software" }))
    await user.click(software.getByRole("link", { name: "landing" }))
    expect(window.location.pathname).toBe("/factories/lines/l-landing")

    await user.click(screen.getByRole("link", { name: "unit 4 on course on cec-moodle" }))
    expect(window.location.pathname).toBe("/factories/lines/l-course")
  })
})
