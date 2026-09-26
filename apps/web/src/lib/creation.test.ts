import { CallError } from "@kinby/contract"
import type { InstanceCreateCommand, OperationGetResult } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { describe, expect, it } from "vitest"

import { type Creation, followCreation, followStart, type Starting } from "@/lib/creation"

const command: InstanceCreateCommand = {
  manifest_id: "Ada",
  persona_name: "Ada",
  model: "openai:gpt-5",
  config: { behavior_prompt: "Answer in haiku." },
  secrets: { api_key: "sk-private" },
  avatar: { shape: "squircle", color: "green" },
}

function operation(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-1",
    instance_id: "instance-1",
    kind: "create",
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

const lostAnswer = () => {
  throw new CallError({
    code: "CONNECTION_LOST",
    message: "The connection to the hub dropped before the call returned.",
    retryable: false,
  })
}

describe("following a creation", () => {
  it("reports each step the hub reaches, then the instance it published", async () => {
    const caller = stubCaller({
      "instance.create": () => ({ operation_id: "op-1", instance_id: "instance-1" }),
      "operation.get": polls(
        operation({ steps: [{ name: "image", state: "running", detail: "Preparing." }] }),
        operation({
          state: "succeeded",
          steps: [
            { name: "image", state: "succeeded", detail: "Preparing." },
            { name: "publish", state: "succeeded", detail: "Instance prepared and stopped." },
          ],
        }),
      ),
    })
    const clock = fakeClock()
    const reports: Creation[] = []

    followCreation(caller, command, (creation) => reports.push(creation), clock)
    await clock.advance(1_000)

    expect(reports.map((report) => report.state)).toEqual(["creating", "created"])
    expect(reports.at(-1)).toMatchObject({ state: "created", instanceId: "instance-1" })
    expect(caller.calls[0]).toEqual({ method: "instance.create", params: command })
  })

  it("reports the fields the hub refused, and follows nothing", async () => {
    const caller = stubCaller({
      "instance.create": () => {
        throw new CallError({
          code: "INVALID_SETUP",
          message: "Some setup values are missing or invalid.",
          retryable: false,
          fields: { api_key: "API key is required." },
        })
      },
    })
    const clock = fakeClock()
    const reports: Creation[] = []

    followCreation(caller, command, (creation) => reports.push(creation), clock)
    await clock.advance(0)

    expect(reports).toEqual([{ state: "invalid", fields: { api_key: "API key is required." } }])
    expect(caller.calls.map((call) => call.method)).toEqual(["instance.create"])
  })

  it("never creates twice when the hub's answer is lost", async () => {
    const caller = stubCaller({ "instance.create": lostAnswer })
    const clock = fakeClock()
    const reports: Creation[] = []

    followCreation(caller, command, (creation) => reports.push(creation), clock)
    await clock.advance(5_000)

    expect(reports).toHaveLength(1)
    expect(reports[0]).toMatchObject({ state: "failed" })
    expect(reports[0]?.state === "failed" && reports[0].detail).toContain("sidebar")
    expect(caller.calls.map((call) => call.method)).toEqual(["instance.create"])
  })

  it("reports the step that failed with what went wrong", async () => {
    const caller = stubCaller({
      "instance.create": () => ({ operation_id: "op-1", instance_id: "instance-1" }),
      "operation.get": () =>
        operation({
          state: "failed",
          detail: "Cannot connect to the Docker daemon.",
          steps: [
            { name: "image", state: "failed", detail: "Cannot connect to the Docker daemon." },
          ],
        }),
    })
    const clock = fakeClock()
    const reports: Creation[] = []

    followCreation(caller, command, (creation) => reports.push(creation), clock)
    await clock.advance(0)

    expect(reports).toEqual([
      {
        state: "failed",
        detail: "Cannot connect to the Docker daemon.",
        steps: [{ name: "image", state: "failed", detail: "Cannot connect to the Docker daemon." }],
      },
    ])
  })
})

describe("following a start", () => {
  it("reports the start's steps until the instance runs", async () => {
    const caller = stubCaller({
      "instance.start": () => ({ operation_id: "op-2", instance_id: "instance-1" }),
      "operation.get": polls(
        operation({ operation_id: "op-2", kind: "start", steps: [] }),
        operation({ operation_id: "op-2", kind: "start", state: "succeeded" }),
      ),
    })
    const clock = fakeClock()
    const reports: Starting[] = []

    followStart(caller, "instance-1", (starting) => reports.push(starting), clock)
    await clock.advance(1_000)

    expect(reports.map((report) => report.state)).toEqual(["starting", "started"])
  })

  it("finds a start whose answer was lost through the instance's active operation", async () => {
    const caller = stubCaller({
      "instance.start": lostAnswer,
      "instance.status": () => ({
        instance_id: "instance-1",
        process: "starting",
        readiness: "starting",
        active_operation_id: "op-2",
      }),
      "operation.get": () => operation({ operation_id: "op-2", kind: "start", state: "succeeded" }),
    })
    const clock = fakeClock()
    const reports: Starting[] = []

    followStart(caller, "instance-1", (starting) => reports.push(starting), clock)
    await clock.advance(0)

    expect(reports).toEqual([{ state: "started" }])
    expect(caller.calls.map((call) => call.method)).toEqual([
      "instance.start",
      "instance.status",
      "operation.get",
    ])
    expect(caller.calls[2]?.params).toEqual({ operation_id: "op-2" })
  })
})
