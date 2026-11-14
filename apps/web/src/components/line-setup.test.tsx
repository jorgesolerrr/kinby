import { CallError, type LineSetupDescription } from "@kinby/contract"
import { type Answers, fakeClock } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { FactoriesPage } from "@/components/factories-page"
import { hub, instances, workbench, workbenches } from "@/components/factories-testing"

const asked = (fields: Partial<LineSetupDescription>): LineSetupDescription => ({
  browser: false,
  commands: [],
  logins: [],
  secrets: [],
  values: [],
  workbench: [],
  ...fields,
})

const field = (
  name: string,
  label: string,
  kind: "secret" | "config" = "secret",
  required = true,
) => ({ name, label, description: "", kind, type: "text" as const, required })

const refused = (fields: Record<string, string>) =>
  new CallError({
    code: "INVALID_SETUP",
    message: "Some setup values are missing or invalid.",
    retryable: false,
    fields,
  })

const claude = {
  id: "claude",
  label: "Claude subscription",
  description: "",
  command: ["claude", "login"],
  prompt_pattern: "",
  volume: "claude",
}

/** A hub with a fourth workbench, docs, which holds no line, and what setting software up asks. */
const setupAnswers: Answers = {
  "workbench.list": () => ({
    workbenches: [...workbenches, workbench({ workbench_id: "wb-docs", name: "docs" })],
  }),
  "line.setup.describe": ({ workbench_id }) => {
    switch (workbench_id) {
      case undefined:
        return asked({
          workbench: [
            field("name", "Name", "config"),
            field("workspace_source", "Repository", "config", false),
          ],
          secrets: [field("GH_TOKEN", "GitHub token")],
          values: [field("COMMIT_EMAIL", "Commit email", "config")],
          commands: ["setup", "checks"],
        })
      case "wb-kinby":
        throw refused({ factory: 'Factory "software" is set up on workbench "kinby" already.' })
      case "wb-landing":
        return asked({ logins: [claude] })
      case "wb-moodle":
        throw refused({
          factory:
            'Factory "software" requires the coder image, and workbench "cec-moodle" runs the browser image.',
        })
      default:
        return asked({ secrets: [field("GH_TOKEN", "GitHub token")], commands: ["checks"] })
    }
  },
  "line.setup": () => ({
    line: {
      line_id: "l-new",
      factory: "software",
      workbench_id: "wb-docs",
      owner_id: "home",
      model: null,
      usd_per_day: null,
      intake: null,
    },
    operation_id: null,
  }),
}

function openSetup(more: Answers = {}) {
  const caller = hub({ ...setupAnswers, ...more })
  render(<FactoriesPage caller={caller} clock={fakeClock()} instances={instances} />)
  return caller
}

const dialog = () => within(screen.getByRole("dialog"))

/** The workbenches the setup offers, each as its row reads, and whether it can be picked. */
const offered = () =>
  within(dialog().getByRole("list", { name: "Workbenches" }))
    .getAllByRole("button")
    .map((row) => [row.getAttribute("aria-label"), row.textContent, !row.hasAttribute("disabled")])

const setupCall = (caller: ReturnType<typeof hub>) =>
  caller.calls.find(({ method }) => method === "line.setup")?.params

describe("setting up a line", () => {
  beforeEach(() => window.history.replaceState(null, "", "/factories"))

  async function pickFactory(user: ReturnType<typeof userEvent.setup>, name: string) {
    await user.click(await screen.findByRole("button", { name: "Set up a line" }))
    await user.click(dialog().getByRole("button", { name }))
  }

  it("offers only the factories that run on a workbench", async () => {
    const user = userEvent.setup()
    openSetup()

    await user.click(await screen.findByRole("button", { name: "Set up a line" }))

    const factories = within(dialog().getByRole("list", { name: "Factories" }))
    expect(factories.getAllByRole("button").map((row) => row.getAttribute("aria-label"))).toEqual([
      "software",
      "course",
    ])
  })

  it("disables a workbench that cannot take the line and says why", async () => {
    const user = userEvent.setup()
    openSetup()

    await pickFactory(user, "software")

    await waitFor(() =>
      expect(offered()).toEqual([
        ["kinby", 'kinbyFactory "software" is set up on workbench "kinby" already.', false],
        ["landing", "landingIt is not signed in to Claude subscription.", false],
        [
          "cec-moodle",
          'cec-moodleFactory "software" requires the coder image, and workbench "cec-moodle" runs the browser image.',
          false,
        ],
        ["docs", "docscoder · A plain folder", true],
      ]),
    )
  })

  it("sets a line up on a workbench, asking only for what it lacks", async () => {
    const user = userEvent.setup()
    const caller = openSetup()
    await pickFactory(user, "software")

    await user.click(await dialog().findByRole("button", { name: "docs" }))
    expect(dialog().queryByLabelText("Name")).toBeNull()
    expect(dialog().queryByLabelText("Commit email")).toBeNull()
    await user.type(dialog().getByLabelText("GitHub token"), "ghp-1")
    await user.type(
      dialog().getByLabelText("checks commands"),
      "bun run check{enter}{enter}ruff check",
    )
    await user.click(dialog().getByRole("button", { name: "Set up" }))

    await waitFor(() => expect(window.location.pathname).toBe("/factories/lines/l-new"))
    expect(setupCall(caller)).toEqual({
      factory: "software",
      workbench_id: "wb-docs",
      secrets: { GH_TOKEN: "ghp-1" },
      values: {},
      commands: { checks: ["bun run check", "ruff check"] },
    })
  })

  it("creates a workbench and its line in one pass", async () => {
    const user = userEvent.setup()
    const caller = openSetup()
    await pickFactory(user, "software")

    await user.click(await dialog().findByRole("button", { name: "New workbench" }))
    await user.type(dialog().getByLabelText("Name"), "cec-backend")
    await user.type(dialog().getByLabelText(/Repository/), "https://github.com/cec/backend")
    await user.type(dialog().getByLabelText("GitHub token"), "ghp-2")
    await user.type(dialog().getByLabelText("Commit email"), "bot@cec.dev")
    await user.type(dialog().getByLabelText("setup commands"), "uv sync")
    await user.click(dialog().getByRole("button", { name: "Set up" }))

    await waitFor(() => expect(window.location.pathname).toBe("/factories/lines/l-new"))
    expect(setupCall(caller)).toEqual({
      factory: "software",
      workbench: { name: "cec-backend", workspace_source: "https://github.com/cec/backend" },
      secrets: { GH_TOKEN: "ghp-2" },
      values: { COMMIT_EMAIL: "bot@cec.dev" },
      commands: { setup: ["uv sync"], checks: [] },
    })
  })

  it("shows each missing requirement by name when the hub refuses", async () => {
    const user = userEvent.setup()
    openSetup({
      "line.setup": () => {
        throw refused({
          "workbench.name": 'A workbench named "kinby" exists already.',
          workbench:
            'Workbench "kinby" lacks what factory "software" requires: secret "GH_TOKEN", value "COMMIT_EMAIL".',
        })
      },
    })
    await pickFactory(user, "software")
    await user.click(await dialog().findByRole("button", { name: "New workbench" }))

    await user.type(dialog().getByLabelText("Name"), "kinby")
    await user.click(dialog().getByRole("button", { name: "Set up" }))

    expect(await dialog().findByText('A workbench named "kinby" exists already.')).toBeDefined()
    expect(
      dialog().getByText(
        'Workbench "kinby" lacks what factory "software" requires: secret "GH_TOKEN", value "COMMIT_EMAIL".',
      ),
    ).toBeDefined()
    expect(window.location.pathname).toBe("/factories")
  })

  it("goes back to pick another workbench or factory", async () => {
    const user = userEvent.setup()
    openSetup()
    await pickFactory(user, "software")
    await user.click(await dialog().findByRole("button", { name: "docs" }))

    await user.click(dialog().getByRole("button", { name: "Back" }))
    expect(dialog().getByRole("list", { name: "Workbenches" })).toBeDefined()
    await user.click(dialog().getByRole("button", { name: "Back" }))
    expect(dialog().getByRole("list", { name: "Factories" })).toBeDefined()
  })
})
