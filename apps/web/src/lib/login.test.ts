import { CallError } from "@kinby/contract"
import type { OperationGetResult, OperationStep } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { describe, expect, it } from "vitest"

import { followLogin, type SignIn } from "@/lib/login"

const container: OperationStep = {
  name: "container",
  state: "succeeded",
  detail: "Starting the setup container.",
}
const waiting: OperationStep = {
  name: "sign-in",
  state: "running",
  detail: "Waiting for you to sign in to Codex.",
}
const prompt = { url: "https://auth.openai.com/codex/device", code: "ABCD-12345" }

function operation(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-login",
    instance_id: "instance-1",
    kind: "login",
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

describe("following a login", () => {
  it("reports the URL and code while the hub waits, then that the user signed in", async () => {
    const caller = stubCaller({
      "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
      "operation.get": polls(
        operation({ steps: [{ ...container, state: "running" }] }),
        operation({ steps: [container, { ...waiting, prompt }] }),
        operation({ state: "succeeded", detail: "Signed in." }),
      ),
    })
    const clock = fakeClock()
    const reports: SignIn[] = []

    followLogin(caller, "instance-1", "codex", (signIn) => reports.push(signIn), clock)
    await clock.advance(1_000)
    await clock.advance(1_000)

    expect(reports).toEqual([
      { state: "signing-in", prompt: null },
      { state: "signing-in", prompt },
      { state: "signed-in" },
    ])
    expect(caller.calls[0]).toEqual({
      method: "instance.login.start",
      params: { instance_id: "instance-1", login_id: "codex" },
    })
  })

  it("follows the login the hub already runs, without starting another", async () => {
    const caller = stubCaller({
      "operation.get": polls(
        operation({ steps: [container, { ...waiting, prompt }] }),
        operation({ state: "succeeded", detail: "Signed in." }),
      ),
    })
    const clock = fakeClock()
    const reports: SignIn[] = []

    followLogin(caller, "instance-1", "codex", (signIn) => reports.push(signIn), clock, "op-login")
    await clock.advance(1_000)

    expect(reports).toEqual([{ state: "signing-in", prompt }, { state: "signed-in" }])
    expect(caller.calls.map((call) => call.method)).toEqual(["operation.get", "operation.get"])
    expect(caller.calls[0]?.params).toEqual({ operation_id: "op-login" })
  })

  it("reports why the login failed, such as an expired code", async () => {
    const detail = "The code expired. Sign in again for a new one."
    const caller = stubCaller({
      "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
      "operation.get": polls(operation({ state: "failed", detail })),
    })
    const clock = fakeClock()
    const reports: SignIn[] = []

    followLogin(caller, "instance-1", "codex", (signIn) => reports.push(signIn), clock)
    await clock.advance(0)

    expect(reports).toEqual([{ state: "failed", detail }])
  })

  it("asks again when the connection drops, and the hub answers with the running login", async () => {
    let attempts = 0
    const caller = stubCaller({
      "instance.login.start": () => {
        attempts += 1
        if (attempts === 1) {
          throw new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })
        }
        return { operation_id: "op-login", instance_id: "instance-1" }
      },
      "operation.get": polls(operation({ steps: [container, { ...waiting, prompt }] })),
    })
    const clock = fakeClock()
    const reports: SignIn[] = []

    followLogin(caller, "instance-1", "codex", (signIn) => reports.push(signIn), clock)
    await clock.advance(1_000)

    expect(reports).toEqual([{ state: "signing-in", prompt }])
  })

  it("reports a refusal, and nothing more once it is stopped", async () => {
    const refused = stubCaller({
      "instance.login.start": () => {
        throw new CallError({
          code: "NOT_FOUND",
          message: 'Instance "instance-1" declares no login "codex".',
          retryable: false,
        })
      },
    })
    const running = stubCaller({
      "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
      "operation.get": polls(operation({ steps: [container, { ...waiting, prompt }] })),
    })
    const clock = fakeClock()
    const reports: SignIn[] = []
    const followed: SignIn[] = []

    followLogin(refused, "instance-1", "codex", (signIn) => reports.push(signIn), clock)
    const stop = followLogin(
      running,
      "instance-1",
      "codex",
      (signIn) => followed.push(signIn),
      clock,
    )
    await clock.advance(0)
    stop()
    await clock.advance(5_000)

    expect(reports).toEqual([
      { state: "failed", detail: 'Instance "instance-1" declares no login "codex".' },
    ])
    expect(followed).toEqual([{ state: "signing-in", prompt }])
  })
})
