import { act, renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import {
  selectThread,
  selectTurn,
  turnPath,
  useSelectedInstanceId,
  useSelectedThreadId,
  useSelectedTurnId,
} from "@/lib/selection"

function selection() {
  return renderHook(() => ({
    instanceId: useSelectedInstanceId(),
    threadId: useSelectedThreadId(),
    turnId: useSelectedTurnId(),
  })).result
}

describe("the selected turn", () => {
  it("comes back from the path that names it, under its thread and instance", () => {
    window.history.replaceState(null, "", turnPath("hub ada", "thread/1", "turn 2"))

    expect(selection().current).toEqual({
      instanceId: "hub ada",
      threadId: "thread/1",
      turnId: "turn 2",
    })
  })

  it("is selected as one new history entry", () => {
    window.history.replaceState(null, "", "/instances/hub-ada/stats")
    const entries = window.history.length
    const selected = selection()

    act(() => selectTurn("hub-ada", "t1", "turn-2"))

    expect(window.history.length).toBe(entries + 1)
    expect(selected.current).toEqual({ instanceId: "hub-ada", threadId: "t1", turnId: "turn-2" })
  })

  it("is none when the URL names only a thread", () => {
    window.history.replaceState(null, "", "/instances/hub-ada/stats")
    const selected = selection()

    act(() => selectThread("hub-ada", "t1"))

    expect(selected.current).toEqual({ instanceId: "hub-ada", threadId: "t1", turnId: undefined })
  })
})
