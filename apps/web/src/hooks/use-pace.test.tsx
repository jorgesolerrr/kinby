import { CallError } from "@kinby/contract"
import { fakeClock } from "@kinby/contract/testing"
import { renderHook } from "@testing-library/react"
import { StrictMode } from "react"
import { describe, expect, it } from "vitest"

import { usePace } from "@/hooks/use-pace"
import { retried } from "@/lib/operation"

describe("a component's pace", () => {
  it("still asks again under StrictMode, which unmounts and remounts the component once", async () => {
    const clock = fakeClock()
    const { result } = renderHook(() => usePace(clock), { wrapper: StrictMode })
    let calls = 0

    void retried(async () => {
      calls += 1
      throw new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })
    }, result.current)
    await clock.advance(1_000)

    expect(calls).toBe(2)
  })

  it("stops when the component unmounts", () => {
    const { result, unmount } = renderHook(() => usePace(fakeClock()))

    unmount()

    expect(result.current.stopped).toBe(true)
  })
})
