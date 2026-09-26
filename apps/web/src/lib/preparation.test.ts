import { CallError } from "@kinby/contract"
import type { OperationGetResult, OperationStep, PackageDescription } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { describe, expect, it } from "vitest"

import { followPreparation, type Preparation } from "@/lib/preparation"

const building: OperationStep = {
  name: "image",
  state: "running",
  detail: "Building the image, or reusing the one prepared.",
}
const built: OperationStep = { ...building, state: "succeeded" }
const described: OperationStep = {
  name: "describe",
  state: "succeeded",
  detail: "Reading what the image declares with the candidate check.",
}

const vanilla: PackageDescription = {
  display_name: "Vanilla",
  description: "kinby's built-in defaults, with no package.",
  icon: "sparkles",
  version: "0.1.0",
  setup_fields: [
    {
      name: "model",
      label: "Model",
      description: "The model the instance calls.",
      kind: "config",
      type: "text",
      required: true,
    },
  ],
}

function operation(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-1",
    instance_id: null,
    kind: "prepare",
    state: "running",
    detail: "",
    steps: [],
    ...fields,
  }
}

/** The hub's answers to each poll of the operation, in order. The last one repeats. */
function polls(...answers: OperationGetResult[]) {
  return () => (answers.length > 1 ? answers.shift() : answers[0]) as OperationGetResult
}

describe("following a preparation", () => {
  it("reports each step as the hub reaches it, then what the prepared image declares", async () => {
    const caller = stubCaller({
      "image.prepare": () => ({ operation_id: "op-1" }),
      "operation.get": polls(
        operation({ steps: [building] }),
        operation({ state: "succeeded", steps: [built, described] }),
      ),
      "package.describe": () => vanilla,
    })
    const clock = fakeClock()
    const reports: Preparation[] = []

    followPreparation(caller, null, (preparation) => reports.push(preparation), clock)
    await clock.advance(1_000)

    expect(reports).toEqual([
      { state: "preparing", steps: [building] },
      { state: "prepared", steps: [built, described], description: vanilla },
    ])
    expect(caller.calls).toEqual([
      { method: "image.prepare", params: { package: null } },
      { method: "operation.get", params: { operation_id: "op-1" } },
      { method: "operation.get", params: { operation_id: "op-1" } },
      { method: "package.describe", params: { package: null } },
    ])
  })

  it("reports the step that failed, and describes nothing", async () => {
    const failed: OperationStep = {
      name: "describe",
      state: "failed",
      detail: 'Executable "claude" is not on PATH.',
    }
    const caller = stubCaller({
      "image.prepare": () => ({ operation_id: "op-1" }),
      "operation.get": polls(
        operation({ state: "failed", detail: failed.detail, steps: [built, failed] }),
      ),
    })
    const clock = fakeClock()
    const reports: Preparation[] = []

    followPreparation(caller, null, (preparation) => reports.push(preparation), clock)
    await clock.advance(0)

    expect(reports).toEqual([{ state: "failed", steps: [built, failed], detail: failed.detail }])
    expect(caller.calls.map((call) => call.method)).not.toContain("package.describe")
  })

  it("asks again when the connection drops, and the hub answers with the same preparation", async () => {
    let attempts = 0
    const caller = stubCaller({
      "image.prepare": () => {
        attempts += 1
        if (attempts === 1) {
          throw new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })
        }
        return { operation_id: "op-1" }
      },
      "operation.get": polls(operation({ state: "succeeded", steps: [built, described] })),
      "package.describe": () => vanilla,
    })
    const clock = fakeClock()
    const reports: Preparation[] = []

    followPreparation(caller, null, (preparation) => reports.push(preparation), clock)
    await clock.advance(1_000)

    expect(reports).toEqual([
      { state: "prepared", steps: [built, described], description: vanilla },
    ])
  })

  it("reports nothing more once it is stopped", async () => {
    const caller = stubCaller({
      "image.prepare": () => ({ operation_id: "op-1" }),
      "operation.get": polls(operation({ steps: [building] })),
    })
    const clock = fakeClock()
    const reports: Preparation[] = []

    const stop = followPreparation(caller, null, (preparation) => reports.push(preparation), clock)
    await clock.advance(0)
    stop()
    await clock.advance(5_000)

    expect(reports).toEqual([{ state: "preparing", steps: [building] }])
  })
})
