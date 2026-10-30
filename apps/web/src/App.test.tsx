import { createClient } from "@kinby/contract"
import type { InstanceSummary, ThreadSummary } from "@kinby/contract"
import { ACCESS_TOKEN, fakeClock, fakeHub, instanceSummary } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import App from "@/App"

function openApp({
  signedIn,
  instances = [],
  removed = [],
  threads = {},
}: {
  signedIn: boolean
  instances?: InstanceSummary[]
  removed?: InstanceSummary[]
  threads?: Record<string, ThreadSummary[]>
}) {
  const hub = fakeHub({ signedIn, instances, removed, threads })
  const clock = fakeClock()
  const { unmount } = render(
    <App client={createClient("http://hub.test", hub.transport, clock)} clock={clock} />,
  )
  return { hub, clock, unmount }
}

const userMenu = () => screen.findByRole("button", { name: /You/ })

async function signIn(token: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText("Access token"), token)
  await user.click(screen.getByRole("button", { name: "Sign in" }))
}

describe("the app", () => {
  it("loads the shell once the hub accepts the access token", async () => {
    const { hub } = openApp({ signedIn: false })

    await signIn(ACCESS_TOKEN)

    expect(await userMenu()).toBeDefined()
    expect(hub.signedIn).toBe(true)
  })

  it("shows one generic error when the hub refuses the token", async () => {
    openApp({ signedIn: false })

    await signIn("a-guessed-token")

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not sign in. Check the token and that the hub is reachable, then sign in again.",
    )
    expect(screen.getAllByRole("alert")).toHaveLength(1)
    expect(screen.queryByRole("button", { name: /You/ })).toBeNull()
  })

  it("opens straight to the shell while the browser session is open", async () => {
    openApp({ signedIn: true })

    expect(await userMenu()).toBeDefined()
    expect(screen.queryByLabelText("Access token")).toBeNull()
  })

  it("signs out from the user menu, ending the browser session", async () => {
    const { hub } = openApp({ signedIn: true })
    const user = userEvent.setup()

    await user.click(await userMenu())
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }))

    expect(await screen.findByLabelText("Access token")).toBeDefined()
    expect(hub.signedIn).toBe(false)
  })

  it("shows it is reconnecting while the socket is down, and stops once it is back", async () => {
    const { hub, clock } = openApp({ signedIn: true })
    await userMenu()

    act(() => hub.sockets[0]?.drop())

    expect(await screen.findByText("Reconnecting")).toBeDefined()
    expect(await userMenu()).toBeDefined()
    await act(() => clock.advance(16_000))
    expect(screen.queryByText("Reconnecting")).toBeNull()
    expect(hub.sockets).toHaveLength(2)
  })
})

const ada = instanceSummary({
  instance_id: "hub-ada",
  persona_name: "Ada",
  intended_state: "running",
})
const unnamed = instanceSummary({
  instance_id: "hub-unnamed",
  manifest_id: "research",
  intended_state: "stopped",
  process: "stopped",
})

/** An instance's row, the button named by the instance that folds its group. */
const instanceRow = (name: string) => screen.findByRole("button", { name: new RegExp(`^${name}`) })

/** The browser shows or hides the tab, as switching to another one does. */
function showPage(visibility: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: visibility })
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
  })
}

async function instanceGroup(name: string) {
  const group = (await instanceRow(name)).closest("li")
  if (group === null) throw new Error(`${name} is not in a sidebar list`)
  return within(group)
}

function thread(fields: Pick<ThreadSummary, "id"> & Partial<ThreadSummary>): ThreadSummary {
  return {
    title: null,
    created_at: "2026-10-30T10:00:00Z",
    last_activity_at: "2026-10-30T10:00:00Z",
    status: "idle",
    mode: "ask",
    mode_pinned: false,
    archived: false,
    origin: { kind: "user" },
    ...fields,
  }
}

/** The main header, over the open page. */
function mainHeader() {
  const header = screen.getByRole("main").querySelector("header")
  if (header === null) throw new Error("The main panel has no header")
  return within(header)
}

describe("the instances", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("lists every instance in the sidebar by its name, saying which one is stopped", async () => {
    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("button", { name: "Ada" })).toBeDefined()
    expect((await instanceGroup("research")).getByText("stopped")).toBeDefined()
  })

  it("shows the state the runtime observed, not the one the instance is meant to be in", async () => {
    const looping = { ...ada, process: "starting", detail: "restarting" } as const
    const failed = { ...unnamed, intended_state: "running", process: "failed" } as const
    openApp({ signedIn: true, instances: [looping, failed] })

    expect((await instanceGroup("Ada")).getByText("restarting")).toBeDefined()
    expect((await instanceGroup("research")).getByText("failed")).toBeDefined()
    expect(screen.queryByText("running")).toBeNull()
  })

  it("draws each instance's avatar in its shape and palette color", async () => {
    const drawn = { ...ada, avatar: { shape: "squircle", color: "green" } } as const
    openApp({ signedIn: true, instances: [drawn, unnamed] })

    const avatar = (await instanceRow("Ada")).querySelector("[data-slot=avatar]")
    const fallback = avatar?.querySelector("[data-slot=avatar-fallback]")
    expect(avatar?.getAttribute("data-shape")).toBe("squircle")
    expect(fallback?.getAttribute("data-variant")).toBe("green")
    expect(fallback?.textContent).toBe("A")
    const other = (await instanceRow("research")).querySelector("[data-slot=avatar]")
    expect(other?.getAttribute("data-shape")).toBe("circle")
  })

  it("opens an instance's page from its row menu, and unfolds its group", async () => {
    openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()
    expect((await instanceRow("research")).getAttribute("aria-expanded")).toBe("false")

    await user.click(await screen.findByRole("button", { name: "Menu for research" }))
    await user.click(await screen.findByRole("menuitem", { name: "Overview" }))

    expect(window.location.pathname).toBe("/instances/hub-unnamed")
    expect(await screen.findByRole("button", { name: "Start" })).toBeDefined()
    expect((await instanceRow("research")).getAttribute("aria-expanded")).toBe("true")
  })

  it("restores the selection from the URL on a reload or an opened link", async () => {
    window.history.replaceState(null, "", "/instances/hub-unnamed")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("button", { name: "Start" })).toBeDefined()
    expect((await instanceRow("research")).getAttribute("aria-expanded")).toBe("true")
  })

  it("opens the thread the URL names, marked in its group, with its instance and title in the main header", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/threads/t1")

    const { hub } = openApp({
      signedIn: true,
      instances: [ada, unnamed],
      threads: { "hub-ada": [thread({ id: "t1", title: "Deploy notes" })] },
    })

    const open = await (await instanceGroup("Ada")).findByRole("link", { name: "Deploy notes" })
    expect(open.getAttribute("aria-current")).toBe("page")
    expect(mainHeader().getByText("Ada")).toBeDefined()
    expect(mainHeader().getByText("Deploy notes")).toBeDefined()
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    expect(relayed?.sent).toContainEqual(
      expect.objectContaining({
        type: "subscribe",
        method: "thread.subscribe",
        params: { thread_id: "t1", after_sequence: 0 },
      }),
    )
    expect(screen.queryByText(/^Start a thread with/)).toBeNull()
  })

  it("shows no threads column beside a running instance", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText(/^Start a thread with/)).toBeDefined()
    expect(screen.queryByRole("complementary")).toBeNull()
    expect(screen.queryByRole("searchbox", { name: "Search threads" })).toBeNull()
  })

  it("opens a running instance's config panel from its page, and puts it in the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Configure" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/config")
    expect(screen.getByRole("navigation", { name: "Config sections" })).toBeDefined()
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(relayed?.sent).toContainEqual(
      expect.objectContaining({ type: "call", method: "prompt.get", params: { name: "behavior" } }),
    )
  })

  it("opens a running instance's memory from its page, and puts it in the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Memory" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/memory")
    expect(screen.getByRole("tab", { name: "Knowledge graph" })).toBeDefined()
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(relayed?.sent).toContainEqual(
      expect.objectContaining({ type: "call", method: "memory.list", params: { query: "" } }),
    )
  })

  it("opens the memory page on the node a link names", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/memory/2026-09-28-fixed-the-deploy")

    const { hub, clock } = openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("tab", { name: "Knowledge graph" })).toBeDefined()
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    // The page reads before the relayed socket is up, and asks again a second later.
    await act(() => clock.advance(1_000))
    expect(relayed?.sent).toContainEqual(
      expect.objectContaining({
        type: "call",
        method: "memory.open",
        params: { node: "2026-09-28-fixed-the-deploy" },
      }),
    )
  })

  it("asks to start a stopped instance to see its memory", async () => {
    window.history.replaceState(null, "", "/instances/hub-unnamed/memory")

    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText("Start it from its page to see its memory.")).toBeDefined()
    expect(screen.queryByRole("tab", { name: "Knowledge graph" })).toBeNull()
    expect(hub.sockets.map((socket) => socket.url)).not.toContain(
      "ws://hub.test/instances/hub-unnamed/ws",
    )
  })

  it("opens a running instance's stats from its page, and puts it in the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Stats" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/stats")
    // The stats page's own Overview, not the instance's.
    expect(screen.getByRole("tab", { name: "Overview", selected: true })).toBeDefined()
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(relayed?.sent).toContainEqual(
      expect.objectContaining({ type: "call", method: "stats.get" }),
    )
  })

  it("opens the selected instance's thread list from its group, and puts it in the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()

    await user.click(await (await instanceGroup("Ada")).findByRole("link", { name: "All threads" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/threads")
    expect(await screen.findByRole("heading", { name: "Threads" })).toBeDefined()
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(relayed?.sent).toContainEqual(
      expect.objectContaining({
        type: "call",
        method: "thread.list",
        params: { filter: "all", limit: 50 },
      }),
    )
  })

  it("asks to start a stopped instance to see its threads", async () => {
    window.history.replaceState(null, "", "/instances/hub-unnamed/threads")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText("Start it from its page to see its threads.")).toBeDefined()
    await userEvent.setup().click(screen.getByRole("button", { name: "Open research" }))
    expect(window.location.pathname).toBe("/instances/hub-unnamed")
    expect(await screen.findByRole("button", { name: "Start" })).toBeDefined()
  })

  it("asks to start a stopped instance to see its stats", async () => {
    window.history.replaceState(null, "", "/instances/hub-unnamed/stats")

    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText("Start it from its page to see its stats.")).toBeDefined()
    expect(screen.queryByRole("tab", { name: "Overview", selected: true })).toBeNull()
    expect(hub.sockets.map((socket) => socket.url)).not.toContain(
      "ws://hub.test/instances/hub-unnamed/ws",
    )
  })

  it("restores the config panel from the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/config")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("navigation", { name: "Config sections" })).toBeDefined()
    expect(screen.queryByText(/^Start a thread with/)).toBeNull()
  })

  it("keeps the config panel and its connection while an update restarts the instance", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/config")
    const { hub, clock } = openApp({ signedIn: true, instances: [ada, unnamed] })
    await screen.findByRole("navigation", { name: "Config sections" })
    const relayed = () =>
      hub.sockets.filter((socket) => socket.url === "ws://hub.test/instances/hub-ada/ws")

    hub.instances = [{ ...ada, process: "stopped" }, unnamed]
    await act(() => clock.advance(30_000))

    expect(screen.getByRole("navigation", { name: "Config sections" })).toBeDefined()
    expect(relayed()).toHaveLength(1)
  })

  it("opens no threads for a selected instance that is not running", async () => {
    window.history.replaceState(null, "", "/instances/hub-unnamed")

    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("button", { name: "Start" })).toBeDefined()
    expect((await instanceGroup("research")).queryAllByRole("link")).toEqual([])
    expect(hub.sockets.map((socket) => socket.url)).not.toContain(
      "ws://hub.test/instances/hub-unnamed/ws",
    )
  })

  it("selects nothing when the URL names an instance the hub does not have", async () => {
    window.history.replaceState(null, "", "/instances/hub-gone")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("textbox", { name: "Message" })).toBeDefined()
    expect(screen.queryByText(/^Start a thread with/)).toBeNull()
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("aria-current")).toBeNull()
    }
  })

  it("lists the instances again once the connection is back", async () => {
    const { hub, clock } = openApp({ signedIn: true, instances: [ada] })
    expect(await instanceRow("Ada")).toBeDefined()

    act(() => hub.sockets[0]?.drop())
    hub.instances = [ada, unnamed]
    await act(() => clock.advance(16_000))

    expect(await instanceRow("research")).toBeDefined()
  })

  it("lists the instances again when the window regains focus, keeping the list meanwhile", async () => {
    const { hub } = openApp({ signedIn: true, instances: [unnamed] })
    expect((await instanceGroup("research")).getByText("stopped")).toBeDefined()

    // Started from another tab.
    hub.instances = [{ ...unnamed, intended_state: "running", process: "running" }]
    act(() => {
      window.dispatchEvent(new Event("focus"))
    })

    expect(screen.getByText("stopped")).toBeDefined()
    expect(await screen.findByRole("button", { name: "research" })).toBeDefined()
  })

  it("lists the instances again every 30 seconds while the page is visible, and not while hidden", async () => {
    const { hub, clock } = openApp({ signedIn: true, instances: [unnamed] })
    const started = { ...unnamed, intended_state: "running", process: "running" } as const
    const stopped = async () => (await instanceGroup("research")).queryByText("stopped")
    expect(await stopped()).not.toBeNull()

    hub.instances = [started]
    await act(() => clock.advance(29_000))
    expect(await stopped()).not.toBeNull()
    await act(() => clock.advance(1_000))
    expect(await stopped()).toBeNull()

    showPage("hidden")
    hub.instances = [unnamed]
    await act(() => clock.advance(120_000))
    expect(await stopped()).toBeNull()

    showPage("visible")
    await act(() => clock.advance(30_000))
    expect(await stopped()).not.toBeNull()
  })

  it("says the hub has no instances yet", async () => {
    openApp({ signedIn: true })

    expect(await screen.findByText("No instances yet")).toBeDefined()
    expect(screen.queryByRole("complementary")).toBeNull()
  })
})

describe("the sidebar", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("holds New thread and Library in its top band, and the account alone in its foot", async () => {
    openApp({ signedIn: true, instances: [unnamed] })
    const account = await userMenu()
    const inSidebar = (role: "link" | "button") =>
      screen
        .getAllByRole(role)
        .filter((element) => !element.closest("main") && element.ariaLabel !== "Toggle Sidebar")

    expect(inSidebar("link").map((link) => link.textContent)).toEqual([
      "kinby",
      "New thread",
      "Library",
      "New instance",
    ])
    const newInstance = screen.getByRole("link", { name: "New instance" })
    const below = inSidebar("button").filter(
      (button) => newInstance.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING,
    )
    expect(below).toEqual([account])
  })

  it("opens the Library from its top band", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("link", { name: "Library" }))

    expect(window.location.pathname).toBe("/library")
    expect(await screen.findByRole("heading", { name: "Library" })).toBeDefined()
  })

  it("shows the dot while threads or factory runs need you, unfolding the group", async () => {
    openApp({
      signedIn: true,
      instances: [{ ...ada, needs_human: 1 }, unnamed],
      threads: { "hub-ada": [thread({ id: "t1", title: "Deploy notes", status: "failed" })] },
    })

    const group = await instanceGroup("Ada")
    expect(await group.findByText("2 waiting for you")).toBeDefined()
    expect(group.getByRole("link", { name: "Deploy notes" })).toBeDefined()
    expect((await instanceRow("Ada")).getAttribute("aria-expanded")).toBe("true")
    expect((await instanceGroup("research")).queryByText(/waiting for you/)).toBeNull()
  })

  it("starts a thread with an instance from its row menu", async () => {
    const kinby = instanceSummary({ instance_id: "hub-kinby", persona_name: "Kinby", home: true })
    openApp({ signedIn: true, instances: [kinby, ada] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Menu for Ada" }))
    await user.click(await screen.findByRole("menuitem", { name: "New thread with Ada" }))

    expect(window.location.pathname).toBe("/")
    expect(window.location.search).toBe("?to=hub-ada")
    expect((await screen.findByRole("combobox", { name: "Send to" })).textContent).toContain("Ada")
  })

  it("opens an instance's tab from its row menu", async () => {
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Menu for Ada" }))
    await user.click(await screen.findByRole("menuitem", { name: "Config" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/config")
    expect(await screen.findByRole("navigation", { name: "Config sections" })).toBeDefined()
  })

  it("hides from the main header's toggle", async () => {
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()
    const sidebar = () => document.querySelector("[data-slot=sidebar]")
    await instanceRow("Ada")
    expect(sidebar()?.getAttribute("data-state")).toBe("expanded")

    await user.click(mainHeader().getByRole("button", { name: "Toggle Sidebar" }))

    expect(sidebar()?.getAttribute("data-state")).toBe("collapsed")
  })

  it("shows only the toggle in the main header off a thread", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    openApp({ signedIn: true, instances: [ada] })
    await screen.findByText(/^Start a thread with/)

    expect(mainHeader().queryByText("Ada")).toBeNull()
  })

  describe("on a phone", () => {
    let wide: typeof window.matchMedia
    beforeEach(() => {
      wide = window.matchMedia
      window.matchMedia = (query) =>
        Object.assign(wide(query), { matches: query === "(max-width: 767px)" })
    })
    afterEach(() => {
      window.matchMedia = wide
    })

    const drawer = () => screen.queryByRole("dialog", { name: "Sidebar" })

    async function openDrawer() {
      const user = userEvent.setup()
      await user.click(await screen.findByRole("button", { name: "Toggle Sidebar" }))
      return { user, drawer: within(await screen.findByRole("dialog", { name: "Sidebar" })) }
    }

    it("opens as a drawer from the main header, and closes on a pick", async () => {
      openApp({ signedIn: true, instances: [ada] })
      await screen.findByRole("textbox", { name: "Message" })
      expect(drawer()).toBeNull()

      const { user, drawer: opened } = await openDrawer()
      await user.click(opened.getByRole("link", { name: "Library" }))

      expect(window.location.pathname).toBe("/library")
      await waitFor(() => expect(drawer()).toBeNull())
    })

    it("closes on a thread picked in a group", async () => {
      openApp({
        signedIn: true,
        instances: [{ ...ada, home: true }],
        threads: { "hub-ada": [thread({ id: "t1", title: "Deploy notes" })] },
      })
      await screen.findByRole("textbox", { name: "Message" })

      const { user, drawer: opened } = await openDrawer()
      await user.click(await opened.findByRole("link", { name: "Deploy notes" }))

      expect(window.location.pathname).toBe("/instances/hub-ada/threads/t1")
      await waitFor(() => expect(drawer()).toBeNull())
    })

    it("closes on a pick from a row menu", async () => {
      openApp({ signedIn: true, instances: [ada] })
      await screen.findByRole("textbox", { name: "Message" })

      const { user, drawer: opened } = await openDrawer()
      await user.click(opened.getByRole("button", { name: "Menu for Ada" }))
      await user.click(await screen.findByRole("menuitem", { name: "Stats" }))

      expect(window.location.pathname).toBe("/instances/hub-ada/stats")
      await waitFor(() => expect(drawer()).toBeNull())
    })
  })
})

const tabs = (name: string) => within(screen.getByRole("tablist", { name }))

async function relayedCall(hub: ReturnType<typeof fakeHub>, instanceId: string, method: string) {
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
  const relayed = hub.sockets.find(
    (socket) => socket.url === `ws://hub.test/instances/${instanceId}/ws`,
  )
  return relayed?.sent.find((frame) => (frame as { method?: string }).method === method)
}

describe("an instance's tabs", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("lists its pages as tabs, with the open one marked", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText(/^Start a thread with/)).toBeDefined()
    expect(
      tabs("Ada")
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Overview", "Config", "Routines", "Memory", "Stats"])
    expect(tabs("Ada").getByRole("tab", { name: "Overview", selected: true })).toBeDefined()
  })

  it("opens each tab at its own path, and Back moves between them", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()
    await screen.findByText(/^Start a thread with/)

    await user.click(tabs("Ada").getByRole("tab", { name: "Config" }))
    expect(window.location.pathname).toBe("/instances/hub-ada/config")
    expect(await screen.findByRole("navigation", { name: "Config sections" })).toBeDefined()

    await user.click(tabs("Ada").getByRole("tab", { name: "Routines" }))
    expect(window.location.pathname).toBe("/instances/hub-ada/routines")
    expect(tabs("Ada").getByRole("tab", { name: "Routines", selected: true })).toBeDefined()
    expect(await relayedCall(hub, "hub-ada", "routine.list")).toBeDefined()

    act(() => window.history.back())

    await waitFor(() => expect(window.location.pathname).toBe("/instances/hub-ada/config"))
    expect(tabs("Ada").getByRole("tab", { name: "Config", selected: true })).toBeDefined()
    expect(await screen.findByRole("navigation", { name: "Config sections" })).toBeDefined()
  })

  it.each([
    ["config", "Config"],
    ["routines", "Routines"],
    ["memory", "Memory"],
    ["stats", "Stats"],
  ])("restores /%s from the URL", async (path, name) => {
    window.history.replaceState(null, "", `/instances/hub-ada/${path}`)

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(
      (await screen.findByRole("tablist", { name: "Ada" })).querySelector("[aria-selected=true]")
        ?.textContent,
    ).toBe(name)
    expect(screen.queryByText(/^Start a thread with/)).toBeNull()
  })

  it("leaves Routines out of the config panel", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/config")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    const sections = await screen.findByRole("navigation", { name: "Config sections" })
    expect(within(sections).queryByRole("button", { name: "Routines" })).toBeNull()
  })

  it("opens the routine a link names on the Routines tab", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/routines/inbox")

    const { hub, clock } = openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("button", { name: "Back to routines" })).toBeDefined()
    // The editor reads before the relayed socket is up, and asks again a second later.
    await act(() => clock.advance(1_000))
    const read = await relayedCall(hub, "hub-ada", "routine.read")
    expect(read).toEqual(expect.objectContaining({ params: { name: "inbox" } }))
  })

  it("says an instance behind the hub lacks routines on its older core, and opens the update", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/routines")
    const behind = instanceSummary({
      instance_id: "hub-ada",
      persona_name: "Ada",
      intended_state: "running",
      notices: [
        {
          code: "revision_behind",
          message: "The instance runs kinby aaaaaaa, and the hub is at bbbbbbb.",
          instance_revision: "a".repeat(40),
          hub_revision: "b".repeat(40),
        },
      ],
    })
    const { hub, clock } = openApp({ signedIn: true, instances: [behind] })
    const user = userEvent.setup()
    await screen.findByRole("tablist", { name: "Ada" })
    // The section lists before the relayed socket is up, and asks again a second later.
    await act(() => clock.advance(1_000))
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    const listed = relayed?.sent.findLast(
      (frame) => (frame as { method?: string }).method === "routine.list",
    ) as { id: string } | undefined

    act(() =>
      relayed?.receive({
        type: "error",
        id: listed?.id,
        error: {
          code: "NOT_FOUND",
          message: 'Method "routine.list" was not found.',
          retryable: false,
        },
      }),
    )

    expect(await screen.findByText("This instance runs an older core")).toBeDefined()
    await user.click(screen.getByRole("button", { name: "Open Core version" }))
    expect(window.location.pathname).toBe("/instances/hub-ada/config/core")
    expect(await screen.findByRole("heading", { name: "Core version" })).toBeDefined()
  })

  it("lands an old link to a routine in the config panel on the Routines tab, in its place", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/config/routines/inbox")
    const entries = window.history.length

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("button", { name: "Back to routines" })).toBeDefined()
    expect(window.location.pathname).toBe("/instances/hub-ada/routines/inbox")
    expect(window.history.length).toBe(entries)
    expect(tabs("Ada").getByRole("tab", { name: "Routines", selected: true })).toBeDefined()
  })

  it.each(["routines", "memory", "stats", "config"])(
    "asks to start a stopped instance on its %s tab, from Overview",
    async (path) => {
      window.history.replaceState(null, "", `/instances/hub-unnamed/${path}`)

      const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })

      expect(await screen.findByText(`Start it from its page to see its ${path}.`)).toBeDefined()
      expect(tabs("research").getByRole("tab", { selected: true }).textContent).not.toBe("Overview")
      expect(hub.sockets.map((socket) => socket.url)).not.toContain(
        "ws://hub.test/instances/hub-unnamed/ws",
      )
      await userEvent.setup().click(screen.getByRole("button", { name: "Open research" }))
      expect(window.location.pathname).toBe("/instances/hub-unnamed")
      expect(await screen.findByRole("button", { name: "Start" })).toBeDefined()
    },
  )

  it("keeps the threads and views outside the tabs", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/threads")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("heading", { name: "Threads" })).toBeDefined()
    expect(screen.queryByRole("tablist", { name: "Ada" })).toBeNull()
  })
})

const grace = instanceSummary({
  instance_id: "hub-grace",
  persona_name: "Grace",
  intended_state: "removed",
  process: "missing",
})

const settingsDialog = () => screen.findByRole("dialog", { name: "Settings" })

async function openSettings() {
  const user = userEvent.setup()
  await user.click(await userMenu())
  await user.click(await screen.findByRole("menuitem", { name: "Settings" }))
  return within(await settingsDialog())
}

describe("the Settings dialog", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("opens from the account menu, which holds Settings and Sign out and no theme", async () => {
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()

    await user.click(await userMenu())

    const items = await screen.findAllByRole("menuitem")
    expect(items.map((item) => item.textContent)).toEqual(["Settings", "Sign out"])
    expect(screen.queryByRole("menuitemradio")).toBeNull()
  })

  it("opens on General over the open page, and puts it in the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    openApp({ signedIn: true, instances: [ada] })

    const dialog = await openSettings()

    expect(dialog.getByRole("tab", { name: "General", selected: true })).toBeDefined()
    expect(dialog.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "General",
      "Usage",
      "Hub secrets",
      "Channels",
      "Instances",
    ])
    expect(window.location.pathname).toBe("/instances/hub-ada")
    expect(window.location.search).toBe("?settings=general")
  })

  it("sets the theme from General", async () => {
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()
    const dialog = await openSettings()

    await user.click(dialog.getByRole("button", { name: "Dark" }))
    expect(document.documentElement.classList.contains("dark")).toBe(true)
    expect(dialog.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true")

    await user.click(dialog.getByRole("button", { name: "System" }))
    expect(document.documentElement.classList.contains("dark")).toBe(false)
  })

  it("switches sections in place, and Back closes it onto the page under it", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()
    const entries = window.history.length
    const dialog = await openSettings()

    await user.click(dialog.getByRole("tab", { name: "Usage" }))

    expect(await dialog.findByRole("heading", { name: "Usage" })).toBeDefined()
    expect(window.location.search).toBe("?settings=usage")
    expect(window.history.length).toBe(entries + 1)
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    const onHub = hub.sockets.find((socket) => socket.url === "ws://hub.test/ws")
    expect(onHub?.sent).toContainEqual(
      expect.objectContaining({ type: "call", method: "stats.summary" }),
    )

    act(() => window.history.back())

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull())
    expect(window.location.pathname).toBe("/instances/hub-ada")
    expect(window.location.search).toBe("")
    expect(await screen.findByText(/^Start a thread with/)).toBeDefined()
  })

  it("closes from its Close button, dropping the section from the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada?settings=channels")
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()

    await user.click(within(await settingsDialog()).getByRole("button", { name: "Close" }))

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull())
    expect(window.location.pathname).toBe("/instances/hub-ada")
    expect(window.location.search).toBe("")
  })

  it.each([
    ["usage", "Usage"],
    ["secrets", "Secrets"],
    ["channels", "Channels"],
    ["instances", "Removed instances"],
  ])("restores ?settings=%s over the page from the URL", async (section, heading) => {
    window.history.replaceState(null, "", `/instances/hub-ada?settings=${section}`)

    openApp({ signedIn: true, instances: [ada], removed: [grace] })

    const dialog = within(await settingsDialog())
    expect(await dialog.findByRole("heading", { name: heading })).toBeDefined()
    expect(window.location.pathname).toBe("/instances/hub-ada")
  })

  it.each([
    ["/usage", "usage", "Usage"],
    ["/secrets", "secrets", "Secrets"],
    ["/channels", "channels", "Channels"],
    ["/removed", "instances", "Removed instances"],
  ])("lands %s on the home route with its section open", async (path, section, heading) => {
    window.history.replaceState(null, "", path)

    openApp({ signedIn: true, instances: [ada], removed: [grace] })

    const dialog = within(await settingsDialog())
    expect(await dialog.findByRole("heading", { name: heading })).toBeDefined()
    expect(window.location.pathname).toBe("/")
    expect(window.location.search).toBe(`?settings=${section}`)
  })

  it("leaves the hub pages and the removed instances out of the sidebar", async () => {
    openApp({ signedIn: true, instances: [ada], removed: [grace] })

    expect(await instanceRow("Ada")).toBeDefined()
    for (const name of ["Usage", "Secrets", "Channels", "Removed instances"]) {
      expect(screen.queryByRole("link", { name })).toBeNull()
    }
  })

  it("lists its sections down the side, and across the top on a narrow screen", async () => {
    window.history.replaceState(null, "", "/?settings=general")
    const wide = window.matchMedia
    const sectionList = async () => within(await settingsDialog()).getByRole("tablist")

    const shown = openApp({ signedIn: true, instances: [ada] })
    expect((await sectionList()).getAttribute("aria-orientation")).toBe("vertical")
    shown.unmount()

    window.matchMedia = (query) =>
      Object.assign(wide(query), { matches: query === "(max-width: 767px)" })
    try {
      openApp({ signedIn: true, instances: [ada] })
      expect((await sectionList()).getAttribute("aria-orientation")).not.toBe("vertical")
    } finally {
      window.matchMedia = wide
    }
  })
})

describe("the Instances section", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("is listed when nothing is removed, and says so", async () => {
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()
    const dialog = await openSettings()

    await user.click(dialog.getByRole("tab", { name: "Instances" }))

    expect(await dialog.findByText("No removed instances")).toBeDefined()
  })

  it("lists the removed instance once a removal finishes, with no reload", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { clock } = openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()
    expect(await screen.findByRole("button", { name: "Configure" })).toBeDefined()

    await user.click(screen.getByRole("button", { name: "More actions" }))
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }))
    const removing = await screen.findByRole("alertdialog", { name: "Remove Ada?" })
    await user.click(within(removing).getByRole("button", { name: "Remove" }))
    await act(() => clock.advance(0))
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Ada/ })).toBeNull())

    const dialog = await openSettings()
    await user.click(dialog.getByRole("tab", { name: "Instances" }))
    expect(await dialog.findByText("Ada")).toBeDefined()
  })

  it("restores one and lands on it shown stopped with Start", async () => {
    window.history.replaceState(null, "", "/?settings=instances")
    const { clock } = openApp({ signedIn: true, instances: [ada], removed: [grace] })
    const user = userEvent.setup()
    const dialog = within(await settingsDialog())

    await user.click(await dialog.findByRole("button", { name: "Restore" }))
    await act(() => clock.advance(0))

    expect(window.location.pathname).toBe("/instances/hub-grace")
    expect(await screen.findByText("Grace is stopped")).toBeDefined()
    expect(screen.getByRole("button", { name: "Start" })).toBeDefined()
    expect((await instanceRow("Grace")).getAttribute("aria-expanded")).toBe("true")
    expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull()
  })

  it("deletes one permanently, drops its row, and stays open", async () => {
    window.history.replaceState(null, "", "/?settings=instances")
    const { clock } = openApp({ signedIn: true, instances: [ada], removed: [grace] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Delete permanently" }))
    const confirm = within(
      await screen.findByRole("alertdialog", { name: "Delete Grace permanently" }),
    )
    await confirm.findByText("/srv/kinby/instances/hub-grace")
    await user.type(confirm.getByRole("textbox", { name: "Type Grace to confirm" }), "Grace")
    await user.click(confirm.getByRole("button", { name: "Delete permanently" }))
    await act(() => clock.advance(0))

    const dialog = within(await settingsDialog())
    expect(await dialog.findByText("No removed instances")).toBeDefined()
    expect(dialog.queryByText("Grace")).toBeNull()
    expect(window.location.search).toBe("?settings=instances")
  })
})

describe("a page that breaks", () => {
  beforeEach(() => window.history.replaceState(null, "", "/"))

  it("shows an error in its place, keeps the sidebar, and opens the next page picked", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Memory" }))
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    const listed = relayed?.sent.find(
      (frame) => (frame as { method?: string }).method === "memory.list",
    ) as { id: string } | undefined

    // An answer the page cannot read, so it throws while it renders.
    act(() => relayed?.receive({ type: "result", id: listed?.id, result: {} }))

    expect((await screen.findByRole("alert")).textContent).toContain("This page could not be shown")
    expect(screen.getByRole("button", { name: "Reload" })).toBeDefined()
    expect(await instanceRow("Ada")).toBeDefined()
    await user.click(await screen.findByRole("link", { name: "Library" }))
    expect(await screen.findByRole("heading", { name: "Library" })).toBeDefined()
    expect(screen.queryByRole("alert")).toBeNull()
  })
})

describe("creating an instance", () => {
  beforeEach(() => window.history.replaceState(null, "", "/"))

  it("opens the onboarding from the sidebar, at its image step", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("link", { name: "New instance" }))

    expect(await screen.findByRole("heading", { name: "New instance" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Prepare the image" })).toBeDefined()
    expect(window.location.pathname).toBe("/new")
    expect(
      (await screen.findByRole("link", { name: "New instance" })).getAttribute("aria-current"),
    ).toBe("page")
  })
})

describe("the home screen", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })
  afterEach(() => vi.useRealTimers())

  const kinby = instanceSummary({ instance_id: "hub-kinby", persona_name: "Kinby", home: true })
  const stopped = (instance: InstanceSummary) =>
    ({ ...instance, intended_state: "stopped", process: "stopped" }) as const
  const sendTo = () => screen.findByRole("combobox", { name: "Send to" })
  const message = () => screen.findByRole("textbox", { name: "Message" })

  /** The thread.create and thread.turn.start calls an instance's connections were sent, oldest first. */
  async function threadStarts(hub: ReturnType<typeof fakeHub>, instanceId: string) {
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    return hub.sockets
      .filter((socket) => socket.url === `ws://hub.test/instances/${instanceId}/ws`)
      .flatMap((socket) => socket.sent as { method?: string; params?: unknown }[])
      .filter(({ method }) => method === "thread.create" || method === "thread.turn.start")
      .map(({ method, params }) => ({ method, params }))
  }

  it.each([
    [9, "Good morning"],
    [15, "Good afternoon"],
    [21, "Good evening"],
  ])(
    "opens at %i o'clock with %s and the composer, and stays on the root path",
    async (hour, greeting) => {
      vi.useFakeTimers({ toFake: ["Date"], now: new Date(2026, 9, 30, hour) })
      const { clock } = openApp({ signedIn: true, instances: [ada, kinby] })

      expect(await screen.findByRole("heading", { name: greeting })).toBeDefined()
      expect(await message()).toBeDefined()
      await act(() => clock.advance(1_000))
      expect(window.location.pathname).toBe("/")
    },
  )

  it("sends to the running instances only, the home one first picked and marked", async () => {
    openApp({ signedIn: true, instances: [ada, unnamed, kinby] })
    const user = userEvent.setup()

    expect((await sendTo()).textContent).toContain("Kinby (home)")
    await user.click(await sendTo())
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      "Ada",
      "Kinby (home)",
    ])
  })

  it("picks the first running instance when the home one is not running", async () => {
    openApp({ signedIn: true, instances: [unnamed, stopped(kinby), ada] })

    expect((await sendTo()).textContent).toContain("Ada")
  })

  it("picks the instance the URL's to parameter names", async () => {
    window.history.replaceState(null, "", "/?to=hub-ada")

    openApp({ signedIn: true, instances: [ada, kinby] })

    expect((await sendTo()).textContent).toContain("Ada")
  })

  it("starts a thread on the picked instance with the message, and opens it", async () => {
    const { hub } = openApp({ signedIn: true, instances: [kinby, ada] })
    const user = userEvent.setup()

    await user.click(await sendTo())
    await user.click(await screen.findByRole("option", { name: "Ada" }))
    await user.type(await message(), "What is on today?")
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send" }).getAttribute("aria-disabled")).toBe(
        "false",
      ),
    )
    await user.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() =>
      expect(window.location.pathname).toBe("/instances/hub-ada/threads/thread-1"),
    )
    expect(await threadStarts(hub, "hub-ada")).toEqual([
      { method: "thread.create", params: {} },
      {
        method: "thread.turn.start",
        params: { thread_id: "thread-1", message: "What is on today?" },
      },
    ])
    expect(await threadStarts(hub, "hub-kinby")).toEqual([])
  })

  it("is disabled and says why when no instance is running", async () => {
    openApp({ signedIn: true, instances: [unnamed, stopped(kinby)] })

    expect((await message()).hasAttribute("disabled")).toBe(true)
    expect(screen.getByText(/^No instance is running/)).toBeDefined()
  })

  it("points to New instance when the hub has none", async () => {
    openApp({ signedIn: true })
    const user = userEvent.setup()

    expect(await screen.findByText("No instances yet")).toBeDefined()
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull()
    await user.click(screen.getByRole("button", { name: "New instance" }))
    expect(window.location.pathname).toBe("/new")
  })

  it("opens from New thread in the sidebar", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    openApp({ signedIn: true, instances: [ada, kinby] })
    const user = userEvent.setup()
    expect(await screen.findByText(/^Start a thread with/)).toBeDefined()

    await user.click(await screen.findByRole("link", { name: "New thread" }))

    expect(window.location.pathname).toBe("/")
    expect(await message()).toBeDefined()
  })
})
