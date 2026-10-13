import type { FactoryRun } from "@kinby/contract"
import { fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
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

const runs: Record<string, FactoryRun[]> = {
  billing: [
    run({ run_id: "run-1", instance_id: "billing", status: "needs-human", step: "review" }),
    run({ run_id: "run-2", instance_id: "billing", status: "done", work_item: { issue: 9 } }),
  ],
  software: [],
}

function hub() {
  return stubCaller({
    "instance.list": () => ({ instances }),
    "factory.run.list": ({ instance_id }) => ({ runs: runs[instance_id ?? ""] ?? [] }),
  })
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

  it("shows each instance's runs under it and its needs-human count", async () => {
    const caller = hub()
    render(<FactoryInstances client={caller} clock={fakeClock()} factory="software" />)

    const billing = within(await screen.findByRole("region", { name: "billing-coder" }))
    const billingRuns = within(billing.getByRole("list", { name: "Runs of billing-coder" }))
    expect(billing.getByText("1 needing a human")).toBeDefined()
    expect(
      billingRuns.getAllByRole("listitem").map((row) => row.getAttribute("aria-label")),
    ).toEqual(["issue 12: Needs a human at review", "issue 9: Done"])
    const software = within(screen.getByRole("region", { name: "software-coder" }))
    expect(software.getByText("0 needing a human")).toBeDefined()
    expect(software.getByText("No runs yet.")).toBeDefined()
    expect(caller.calls.filter((call) => call.method === "factory.run.list")).toEqual([
      { method: "factory.run.list", params: { factory: "software", instance_id: "billing" } },
      { method: "factory.run.list", params: { factory: "software", instance_id: "software" } },
    ])
  })

  it("says so when no instance holds one of its roles", async () => {
    render(<FactoryInstances client={hub()} clock={fakeClock()} factory="research" />)

    expect(await screen.findByText("No instance holds a role of research yet.")).toBeDefined()
  })
})
