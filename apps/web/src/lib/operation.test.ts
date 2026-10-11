import { CallError } from "@kinby/contract"
import type { OperationGetResult } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { describe, expect, it } from "vitest"

import { finished, pace, retried } from "@/lib/operation"

const dropped = () =>
  new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })

/** A call that loses the connection the first `drops` times it is made, then reads "read". */
function dropping(drops = Infinity) {
  let calls = 0
  return {
    calls: () => calls,
    call: async () => {
      calls += 1
      if (calls <= drops) throw dropped()
      return "read"
    },
  }
}

describe("asking again after a dropped connection", () => {
  it("asks once a second until the call goes through", async () => {
    const clock = fakeClock()
    const { call } = dropping(2)

    const asked = retried(call, pace(clock))
    await clock.advance(1_000)
    await clock.advance(1_000)

    await expect(asked).resolves.toBe("read")
  })

  it("passes on an error that is not a dropped connection", async () => {
    const refused = new CallError({ code: "NOT_FOUND", message: "gone", retryable: false })

    await expect(retried(() => Promise.reject(refused), pace(fakeClock()))).rejects.toBe(refused)
  })

  it("asks no more once its pace stops while a call is in flight", async () => {
    const clock = fakeClock()
    const pacing = pace(clock)
    const { call, calls } = dropping()

    void retried(call, pacing)
    pacing.stop()
    await clock.advance(10_000)

    expect(calls()).toBe(1)
  })

  it("stops every loop waiting on a pace when it stops", async () => {
    const clock = fakeClock()
    const pacing = pace(clock)
    const { call, calls } = dropping()

    void retried(call, pacing)
    void retried(call, pacing)
    await clock.advance(0)
    pacing.stop()
    await clock.advance(10_000)

    expect(calls()).toBe(2)
  })
})

describe("polling an operation", () => {
  const operation = (state: OperationGetResult["state"]): OperationGetResult => ({
    operation_id: "op-1",
    instance_id: "instance-1",
    kind: "login",
    state,
    detail: "",
    steps: [],
  })

  it("reads the operation once a second until it finishes", async () => {
    const clock = fakeClock()
    const answers = [operation("running"), operation("running"), operation("succeeded")]
    const caller = stubCaller({ "operation.get": () => answers.shift() ?? operation("failed") })

    const polled = finished(caller, "op-1", pace(clock), () => {})
    await clock.advance(1_000)
    await clock.advance(1_000)

    await expect(polled).resolves.toEqual(operation("succeeded"))
  })

  it("stops polling when its pace stops while a read is in flight", async () => {
    const clock = fakeClock()
    const pacing = pace(clock)
    const caller = stubCaller({ "operation.get": () => operation("running") })

    void finished(caller, "op-1", pacing, () => {})
    pacing.stop()
    await clock.advance(10_000)

    expect(caller.calls).toHaveLength(1)
  })
})
