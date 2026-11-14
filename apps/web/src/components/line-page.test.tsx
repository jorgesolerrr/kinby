import type { Workbench } from "@kinby/contract"
import { fakeClock } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { hub, instances, workbench, workbenches } from "@/components/factories-testing"
import { LinePage } from "@/components/line-page"
import { rows } from "@/components/stats-testing"
import { when } from "@/lib/config-changes"

const kinby = workbenches[0] as Workbench

/** The line's detail by its term, as the page lists them. */
const detail = (term: string) => screen.getByText(term, { selector: "dt" }).nextElementSibling

describe("a line's page", () => {
  beforeEach(() => window.history.replaceState(null, "", "/factories/lines/l-kinby"))

  it("shows the line's owner, model, daily budget and intake", async () => {
    const user = userEvent.setup()
    render(<LinePage caller={hub()} clock={fakeClock()} lineId="l-kinby" instances={instances} />)

    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("software on kinby")
    expect(detail("Model")?.textContent).toBe("claude-opus-5-5")
    expect(detail("Daily budget")?.textContent).toBe("$20.00 a day")
    expect(detail("Intake")?.textContent).toBe("software-kinby-intake")
    await user.click(within(detail("Owner") as HTMLElement).getByRole("link", { name: "Kinby" }))
    expect(window.location.pathname).toBe("/instances/home")
  })

  it("lists every run of the line with its status, step and start", async () => {
    render(<LinePage caller={hub()} clock={fakeClock()} lineId="l-kinby" instances={instances} />)

    const runs = await screen.findByRole("table")
    const started = when("2026-10-12T09:00:00Z")
    expect(rows(runs)).toEqual([
      ["issue 1305", "Needs a human", "implement", started],
      ["issue 1310", "Running", "implement", started],
      ["issue 1290", "Done", "", started],
    ])
  })

  it("shows its workbench: state, image, workspace, commands, what the factory requires, and the other lines", async () => {
    const user = userEvent.setup()
    render(<LinePage caller={hub()} clock={fakeClock()} lineId="l-kinby" instances={instances} />)

    await user.click(await screen.findByRole("tab", { name: "Workbench" }))

    expect(detail("State")?.textContent).toBe("Running")
    expect(detail("Image")?.textContent).toBe("coder")
    expect(detail("Workspace")?.textContent).toBe("https://github.com/jorgesolerrr/kinby")
    const commands = within(screen.getByRole("region", { name: "Repository commands" }))
    expect(
      commands.getByText("uv sync bun install", {
        normalizer: (text) => text.replace(/\s+/g, " "),
      }),
    ).toBeDefined()
    const requires = within(screen.getByRole("list", { name: "What software requires" }))
    expect(requires.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "GitHub tokenHeld",
      "npm tokenMissing",
      "setup commandsHeld",
      "checks commandsHeld",
      "lint commandsMissing",
    ])
    const others = within(screen.getByRole("region", { name: "Other lines on kinby" }))
    await user.click(others.getByRole("link", { name: "course" }))
    expect(window.location.pathname).toBe("/factories/lines/l-notes")
  })

  it("stops, starts and recreates its workbench", async () => {
    const user = userEvent.setup()
    let held: Workbench = kinby
    const operation = (state: Workbench["intended_state"]) => () => {
      held = {
        ...held,
        intended_state: state,
        process: state === "running" ? "running" : "stopped",
      }
      return { operation_id: "op-1", workbench_id: held.workbench_id }
    }
    const caller = hub({
      "workbench.get": () => held,
      "workbench.stop": operation("stopped"),
      "workbench.start": operation("running"),
      "workbench.recreate": operation("running"),
      "operation.get": ({ operation_id }) => ({
        operation_id,
        instance_id: "wb-kinby",
        kind: "stop",
        state: "succeeded",
        detail: "",
        steps: [],
      }),
    })
    render(<LinePage caller={caller} clock={fakeClock()} lineId="l-kinby" instances={instances} />)
    await user.click(await screen.findByRole("tab", { name: "Workbench" }))

    await user.click(screen.getByRole("button", { name: "Stop" }))
    await waitFor(() => expect(detail("State")?.textContent).toBe("Stopped"))
    await user.click(screen.getByRole("button", { name: "Start" }))
    await waitFor(() => expect(detail("State")?.textContent).toBe("Running"))
    await user.click(screen.getByRole("button", { name: "Recreate" }))

    await waitFor(() =>
      expect(
        caller.calls
          .filter(({ method }) => method.startsWith("workbench.") && method !== "workbench.get")
          .map(({ method, params }) => [method, params]),
      ).toEqual([
        ["workbench.stop", { workbench_id: "wb-kinby" }],
        ["workbench.start", { workbench_id: "wb-kinby" }],
        ["workbench.recreate", { workbench_id: "wb-kinby" }],
      ]),
    )
  })

  it("says why a workbench operation failed", async () => {
    const user = userEvent.setup()
    const caller = hub({
      "workbench.get": () => workbench({ workbench_id: "wb-kinby", name: "kinby" }),
      "workbench.recreate": () => ({ operation_id: "op-1", workbench_id: "wb-kinby" }),
      "operation.get": ({ operation_id }) => ({
        operation_id,
        instance_id: "wb-kinby",
        kind: "recreate",
        state: "failed",
        detail: "The image is gone.",
        steps: [],
      }),
    })
    render(<LinePage caller={caller} clock={fakeClock()} lineId="l-kinby" instances={instances} />)
    await user.click(await screen.findByRole("tab", { name: "Workbench" }))

    await user.click(screen.getByRole("button", { name: "Recreate" }))

    expect((await screen.findByRole("alert")).textContent).toContain("The image is gone.")
  })
})
