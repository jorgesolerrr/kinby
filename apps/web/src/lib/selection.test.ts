import { act, renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import {
  closeSettings,
  landOn,
  linkedNode,
  linkedRoutine,
  openSettings,
  replaceOldPath,
  selectThread,
  switchSettings,
  selectTurn,
  turnPath,
  useSelectedInstanceId,
  useSelectedThreadId,
  useSelectedTurnId,
  useSettingsOpen,
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

describe("the open Settings section", () => {
  const settings = () => renderHook(() => useSettingsOpen()).result

  it("comes back from the URL, over the path that is open", () => {
    window.history.replaceState(null, "", "/instances/hub-ada?settings=channels")

    expect(settings().current).toBe("channels")
    expect(selection().current.instanceId).toBe("hub-ada")
  })

  it("is none when the URL names no section, or one Settings does not have", () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    expect(settings().current).toBeUndefined()

    window.history.replaceState(null, "", "/?settings=billing")
    expect(settings().current).toBeUndefined()
  })

  it("opens as one new history entry, on General unless one is named", () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const entries = window.history.length
    const open = settings()

    act(() => openSettings())

    expect(window.history.length).toBe(entries + 1)
    expect(open.current).toBe("general")
    expect(window.location.pathname).toBe("/instances/hub-ada")
  })

  it("switches in place of the current history entry", () => {
    window.history.replaceState(null, "", "/instances/hub-ada?settings=general")
    const entries = window.history.length
    const open = settings()

    act(() => switchSettings("usage"))

    expect(window.history.length).toBe(entries)
    expect(open.current).toBe("usage")
    expect(window.location.pathname).toBe("/instances/hub-ada")
  })

  it("closes by dropping the parameter and keeping the path", () => {
    window.history.replaceState(null, "", "/instances/hub-ada?settings=secrets")
    const open = settings()

    act(() => closeSettings())

    expect(open.current).toBeUndefined()
    expect(window.location.pathname).toBe("/instances/hub-ada")
    expect(window.location.search).toBe("")
  })

  it("stays open when the page under it lands on an instance", () => {
    window.history.replaceState(null, "", "/?settings=usage")

    act(() => landOn("hub-kinby", "t2"))

    expect(window.location.pathname).toBe("/instances/hub-kinby/threads/t2")
    expect(settings().current).toBe("usage")
  })
})

describe("an old hub page's path", () => {
  it.each([
    ["/usage", "usage"],
    ["/secrets", "secrets"],
    ["/channels", "channels"],
    ["/removed", "instances"],
  ])("%s gives way to the root path with Settings on its section", (path, section) => {
    window.history.replaceState(null, "", path)
    const entries = window.history.length

    replaceOldPath()

    expect(window.history.length).toBe(entries)
    expect(window.location.pathname).toBe("/")
    expect(renderHook(() => useSettingsOpen()).result.current).toBe(section)
  })

  it("leaves any other path as it is", () => {
    window.history.replaceState(null, "", "/library")

    replaceOldPath()

    expect(window.location.pathname).toBe("/library")
    expect(window.location.search).toBe("")
  })
})

describe("a malformed URL", () => {
  it("selects no instance", () => {
    window.history.replaceState(null, "", "/instances/50%")

    expect(selection().current.instanceId).toBeUndefined()
  })

  it("selects no thread", () => {
    window.history.replaceState(null, "", "/instances/a/threads/%zz")

    expect(selection().current).toEqual({
      instanceId: "a",
      threadId: undefined,
      turnId: undefined,
    })
  })

  it("selects no turn", () => {
    window.history.replaceState(null, "", "/instances/a/threads/t1/turns/%E0%A4%A")

    expect(selection().current).toEqual({ instanceId: "a", threadId: "t1", turnId: undefined })
  })

  it("links no routine", () => {
    window.history.replaceState(null, "", "/instances/a/config/routines/50%")

    expect(linkedRoutine()).toBeUndefined()
  })

  it("links no memory node", () => {
    window.history.replaceState(null, "", "/instances/a/memory/%zz")

    expect(linkedNode()).toBeUndefined()
  })
})
