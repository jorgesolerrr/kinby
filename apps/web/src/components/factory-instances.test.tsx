import { CallError, type FactoryRun } from "@kinby/contract"
import { fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { FactoryInstances } from "@/components/factory-instances"

const coder = { factory: "software", name: "coder" }

function run(fields: Pick<FactoryRun, "run_id" | "instance_id" | "status"> & Partial<FactoryRun>) {
  return {
    factory: "software",
    step: null,
    work_item: { issue: 12 },
    created_at: "2026-10-12T09:00:00Z",
    updated_at: "2026-10-12T09:00:00Z",
    ...fields,
  }
}

const instances = [
  instanceSummary({ instance_id: "home", manifest_id: "home" }),
  instanceSummary({
    instance_id: "billing",
    manifest_id: "billing-coder",
    factory_role: coder,
    needs_human: 1,
  }),
  instanceSummary({ instance_id: "software", manifest_id: "software-coder", factory_role: coder }),
  instanceSummary({
    instance_id: "tickets",
    manifest_id: "tickets-coder",
    factory_role: { factory: "tickets", name: "coder" },
  }),
]

const stopped = run({
  run_id: "run-1",
  instance_id: "billing",
  status: "needs-human",
  step: "review",
})
const runs: Record<string, FactoryRun[]> = {
  billing: [
    run({ run_id: "run-2", instance_id: "billing", status: "done", work_item: { issue: 9 } }),
    stopped,
  ],
  software: [],
}

const attempt = {
  attempt: 1,
  ended_at: "2026-10-12T09:30:00Z",
  ending: "failed" as const,
  outcome: null,
  started_at: "2026-10-12T09:10:00Z",
  values: {},
}

/** The hub, which lists the run each action answers in place of the stopped one. */
function hub(acted: () => FactoryRun = () => stopped) {
  let listed = runs
  const act = () => {
    const moved = acted()
    listed = { ...runs, billing: [moved] }
    return moved
  }
  return stubCaller({
    "instance.list": () => ({ instances }),
    "factory.run.list": ({ instance_id }) => ({ runs: listed[instance_id ?? ""] ?? [] }),
    "factory.run.get": ({ run_id }) => ({
      run: stopped,
      attempts: [
        { ...attempt, step: "implement", summary: "Implemented it." },
        { ...attempt, step: "review", summary: "Step review failed twice, its retries." },
      ],
      earlier_steps: run_id === stopped.run_id ? ["prepare", "implement"] : [],
    }),
    "factory.run.retry": act,
    "factory.run.send_back": act,
    "factory.run.cancel": act,
  })
}

async function stoppedRun() {
  const billing = within(await screen.findByRole("region", { name: "billing-coder" }))
  return within(await billing.findByRole("listitem", { name: "issue 12: Needs a human at review" }))
}

describe("the factory's instances", () => {
  it("lists each instance that holds one of its roles, with its role", async () => {
    render(<FactoryInstances client={hub()} clock={fakeClock()} factory="software" />)

    const listed = await screen.findAllByRole("region")
    expect(listed.map((region) => region.getAttribute("aria-label"))).toEqual([
      "billing-coder",
      "software-coder",
    ])
    expect(within(listed[0] as HTMLElement).getByText("coder")).toBeDefined()
  })

  it("shows each instance's runs under it, those needing a human first with their cause", async () => {
    const caller = hub()
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    const billing = within(await screen.findByRole("region", { name: "billing-coder" }))
    const billingRuns = within(billing.getByRole("list", { name: "Runs of billing-coder" }))
    expect(billing.getByText("1 needing a human")).toBeDefined()
    expect(
      billingRuns.getAllByRole("listitem").map((row) => row.getAttribute("aria-label")),
    ).toEqual(["issue 12: Needs a human at review", "issue 9: Done"])
    expect(await billingRuns.findByText("Step review failed twice, its retries.")).toBeDefined()
    const done = within(billingRuns.getByRole("listitem", { name: "issue 9: Done" }))
    expect(done.queryByRole("button")).toBeNull()
    const software = within(screen.getByRole("region", { name: "software-coder" }))
    expect(software.getByText("0 needing a human")).toBeDefined()
    expect(software.getByText("No runs yet.")).toBeDefined()
    expect(caller.calls.filter((call) => call.method === "factory.run.list")).toEqual([
      { method: "factory.run.list", params: { factory: "software", instance_id: "billing" } },
      { method: "factory.run.list", params: { factory: "software", instance_id: "software" } },
    ])
  })

  it("retries a run that needs a human at its step", async () => {
    const caller = hub(() => run({ ...stopped, status: "queued" }))
    const user = userEvent.setup()
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    await user.click((await stoppedRun()).getByRole("button", { name: "Retry" }))

    expect(
      await screen.findByRole("listitem", { name: "issue 12: Queued at review" }),
    ).toBeDefined()
    expect(caller.calls).toContainEqual({
      method: "factory.run.retry",
      params: { run_id: "run-1" },
    })
  })

  it("sends a run that needs a human back to the earlier step picked", async () => {
    const caller = hub(() => run({ ...stopped, status: "queued", step: "implement" }))
    const user = userEvent.setup()
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    await user.click((await stoppedRun()).getByRole("button", { name: "Send back" }))
    const steps = await screen.findAllByRole("menuitem")
    expect(steps.map((step) => step.textContent)).toEqual(["prepare", "implement"])
    await user.click(screen.getByRole("menuitem", { name: "implement" }))

    expect(
      await screen.findByRole("listitem", { name: "issue 12: Queued at implement" }),
    ).toBeDefined()
    expect(caller.calls).toContainEqual({
      method: "factory.run.send_back",
      params: { run_id: "run-1", step: "implement" },
    })
  })

  it("cancels a run that needs a human once the user confirms", async () => {
    const caller = hub(() => run({ ...stopped, status: "cancelled" }))
    const user = userEvent.setup()
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    await user.click((await stoppedRun()).getByRole("button", { name: "Cancel" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Cancel the run of issue 12?" })
    await user.click(within(dialog).getByRole("button", { name: "Cancel run" }))

    expect(
      await screen.findByRole("listitem", { name: "issue 12: Cancelled at review" }),
    ).toBeDefined()
    expect(caller.calls).toContainEqual({
      method: "factory.run.cancel",
      params: { run_id: "run-1" },
    })
  })

  it("says why the hub refused an action", async () => {
    const caller = hub(() => {
      throw new CallError({
        code: "INVALID_ARGUMENT",
        message: 'Factory run "run-1" is queued, so it needs no human.',
        retryable: false,
      })
    })
    const user = userEvent.setup()
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    const row = await stoppedRun()
    await user.click(row.getByRole("button", { name: "Retry" }))

    await waitFor(() =>
      expect(row.getByText('Factory run "run-1" is queued, so it needs no human.')).toBeDefined(),
    )
  })

  it("closes the cancel dialog on confirming, so a refusal shows under the run", async () => {
    const caller = hub(() => {
      throw new CallError({
        code: "INVALID_ARGUMENT",
        message: 'Factory run "run-1" is queued, so it needs no human.',
        retryable: false,
      })
    })
    const user = userEvent.setup()
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    const row = await stoppedRun()
    await user.click(row.getByRole("button", { name: "Cancel" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Cancel the run of issue 12?" })
    await user.click(within(dialog).getByRole("button", { name: "Cancel run" }))

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
    expect(row.getByText('Factory run "run-1" is queued, so it needs no human.')).toBeDefined()
  })

  it("shows a run parked until its plan window resets with the time it wakes", async () => {
    const parked = run({
      run_id: "run-3",
      instance_id: "billing",
      status: "parked",
      step: "implement",
      wakes_at: new Date(2026, 9, 20, 15, 0).toISOString(),
    })
    const caller = stubCaller({
      "instance.list": () => ({ instances }),
      "factory.run.list": ({ instance_id }) => ({
        runs: instance_id === "billing" ? [parked] : [],
      }),
    })
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    const billing = within(await screen.findByRole("region", { name: "billing-coder" }))
    const row = await billing.findByRole("listitem", {
      name: "issue 12: Parked at implement until Oct 20, 2026, 3:00 PM",
    })
    expect(within(row).queryByRole("button")).toBeNull()
  })

  it("says so when no instance holds one of its roles", async () => {
    render(<FactoryInstances client={hub()} clock={fakeClock()} factory="research" />)

    expect(await screen.findByText("No instance holds a role of research yet.")).toBeDefined()
  })
})
