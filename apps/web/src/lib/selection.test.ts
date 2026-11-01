import { act, renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import {
  closePane,
  closeSettings,
  homePath,
  landOn,
  linkedNode,
  linkedRoutine,
  openPane,
  openSettings,
  openTab,
  replaceOldPath,
  routinePath,
  selectThread,
  switchSettings,
  selectTurn,
  turnPath,
  useInstanceTab,
  useOpenPane,
  useSelectedInstanceId,
  useSelectedThreadId,
  useSelectedTurnId,
  useSendTo,
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

describe("the open tab of an instance's page", () => {
  const tab = () => renderHook(() => useInstanceTab()).result

  it.each([
    ["/instances/hub-ada", "overview"],
    ["/instances/hub-ada/config", "config"],
    ["/instances/hub-ada/config/core", "config"],
    ["/instances/hub-ada/routines", "routines"],
    ["/instances/hub-ada/routines/inbox", "routines"],
    ["/instances/hub-ada/memory", "memory"],
    ["/instances/hub-ada/memory/a-node", "memory"],
    ["/instances/hub-ada/stats", "stats"],
  ])("comes back from %s as %s", (path, open) => {
    window.history.replaceState(null, "", path)

    expect(tab().current).toBe(open)
  })

  it.each([
    "/instances/hub-ada/threads",
    "/instances/hub-ada/threads/t1",
    "/instances/hub-ada/views/v1",
    "/library",
    "/",
  ])("is none on %s, which is not one of the tabs", (path) => {
    window.history.replaceState(null, "", path)

    expect(tab().current).toBeUndefined()
  })

  it("opens as one new history entry at the tab's path", () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const entries = window.history.length
    const open = tab()

    act(() => openTab("hub-ada", "routines"))

    expect(window.history.length).toBe(entries + 1)
    expect(window.location.pathname).toBe("/instances/hub-ada/routines")
    expect(open.current).toBe("routines")

    act(() => openTab("hub-ada", "overview"))

    expect(window.location.pathname).toBe("/instances/hub-ada")
    expect(open.current).toBe("overview")
  })
})

describe("a linked routine", () => {
  it("comes back from its path on the Routines tab", () => {
    window.history.replaceState(null, "", routinePath("hub ada", "inbox/2"))

    expect(window.location.pathname).toBe("/instances/hub%20ada/routines/inbox%2F2")
    expect(linkedRoutine()).toBe("inbox/2")
  })

  it("is none on the Routines tab with no routine named", () => {
    window.history.replaceState(null, "", "/instances/hub-ada/routines")

    expect(linkedRoutine()).toBeUndefined()
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

describe("the pane open beside a thread", () => {
  const pane = () => renderHook(() => useOpenPane()).result
  const thread = "/instances/hub-ada/threads/t1"

  it("comes back from the URL, over the thread that is open", () => {
    window.history.replaceState(null, "", `${thread}?pane=takeover`)

    expect(pane().current).toBe("takeover")
    expect(selection().current.threadId).toBe("t1")
  })

  it("is none when the URL names no pane, or one the thread does not have", () => {
    window.history.replaceState(null, "", thread)
    expect(pane().current).toBeUndefined()

    window.history.replaceState(null, "", `${thread}?pane=canvas`)
    expect(pane().current).toBeUndefined()
  })

  it("opens as one new history entry, keeping the thread and the Settings dialog", () => {
    window.history.replaceState(null, "", `${thread}?settings=usage`)
    const entries = window.history.length
    const open = pane()

    act(() => openPane("takeover"))

    expect(window.history.length).toBe(entries + 1)
    expect(open.current).toBe("takeover")
    expect(window.location.pathname).toBe(thread)
    expect(new URLSearchParams(window.location.search).get("settings")).toBe("usage")
  })

  it("closes by dropping the parameter and keeping the thread, in the same history entry", () => {
    window.history.replaceState(null, "", `${thread}?pane=takeover`)
    const entries = window.history.length
    const open = pane()

    act(() => closePane())

    expect(window.history.length).toBe(entries)
    expect(open.current).toBeUndefined()
    expect(window.location.pathname).toBe(thread)
    expect(window.location.search).toBe("")
  })

  it("closes when another thread opens", () => {
    window.history.replaceState(null, "", `${thread}?pane=takeover`)
    const open = pane()

    act(() => selectThread("hub-ada", "t2"))

    expect(open.current).toBeUndefined()
  })
})

describe("the instance the home screen sends to", () => {
  const sendTo = () => renderHook(() => useSendTo()).result

  it("comes back from the URL's to parameter, with the root path selecting no instance", () => {
    window.history.replaceState(null, "", "/?to=hub%20ada")

    expect(sendTo().current).toBe("hub ada")
    expect(selection().current.instanceId).toBeUndefined()
  })

  it("is none when the URL names none", () => {
    window.history.replaceState(null, "", "/?settings=general")

    expect(sendTo().current).toBeUndefined()
  })

  it("comes back from the home screen's path preset to it", () => {
    window.history.replaceState(null, "", homePath("hub ada"))

    expect(window.location.pathname).toBe("/")
    expect(sendTo().current).toBe("hub ada")
  })
})

describe("an old path", () => {
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

  it("gives way to the Routines tab when it links a routine in the config panel", () => {
    window.history.replaceState(null, "", "/instances/hub-ada/config/routines/inbox?settings=usage")
    const entries = window.history.length

    replaceOldPath()

    expect(window.history.length).toBe(entries)
    expect(window.location.pathname).toBe("/instances/hub-ada/routines/inbox")
    expect(window.location.search).toBe("?settings=usage")
    expect(linkedRoutine()).toBe("inbox")
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
    window.history.replaceState(null, "", "/instances/a/routines/50%")

    expect(linkedRoutine()).toBeUndefined()
  })

  it("links no memory node", () => {
    window.history.replaceState(null, "", "/instances/a/memory/%zz")

    expect(linkedNode()).toBeUndefined()
  })
})
