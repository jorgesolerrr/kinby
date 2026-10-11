import { CallError } from "@kinby/contract"
import { fakeClock } from "@kinby/contract/testing"
import { act, renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { useRead } from "@/hooks/use-read"

/** A read that always loses the connection, counting how often it was made. */
function dropping() {
  const made = { calls: 0 }
  const read = async () => {
    made.calls += 1
    throw new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })
  }
  return { read, made }
}

describe("reading while the connection is lost", () => {
  it("keeps one read asking again after the window regains focus", async () => {
    const clock = fakeClock()
    const { read, made } = dropping()
    renderHook(() => useRead(read, clock))
    await clock.advance(0)

    act(() => {
      window.dispatchEvent(new Event("focus"))
    })
    await clock.advance(0)
    const afterFocus = made.calls
    await clock.advance(1_000)

    expect(made.calls - afterFocus).toBe(1)
  })

  it("asks no more once the component unmounts", async () => {
    const clock = fakeClock()
    const { read, made } = dropping()
    const { unmount } = renderHook(() => useRead(read, clock))
    await clock.advance(0)

    unmount()
    await clock.advance(10_000)

    expect(made.calls).toBe(1)
  })
})
