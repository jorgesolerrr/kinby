import { createClient } from "@kinby/contract"
import type { InstanceSummary } from "@kinby/contract"
import { ACCESS_TOKEN, fakeClock, fakeHub, instanceSummary } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import App from "@/App"

function openApp({
  signedIn,
  instances = [],
}: {
  signedIn: boolean
  instances?: InstanceSummary[]
}) {
  const hub = fakeHub({ signedIn, instances })
  const clock = fakeClock()
  render(<App client={createClient("http://hub.test", hub.transport, clock)} clock={clock} />)
  return { hub, clock }
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

    expect((await screen.findByRole("alert")).textContent).toBe("Could not sign in.")
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

const instanceLink = (name: string) => screen.findByRole("link", { name })

/** The browser shows or hides the tab, as switching to another one does. */
function showPage(visibility: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: visibility })
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
  })
}

async function instanceState(name: string) {
  const row = (await instanceLink(name)).closest("li")
  if (row === null) throw new Error(`${name} is not in a sidebar list`)
  return within(row)
}

describe("the instances", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/")
    Reflect.deleteProperty(document, "visibilityState")
  })

  it("lists every instance in the sidebar with its name and state", async () => {
    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect((await instanceState("Ada")).getByText("running")).toBeDefined()
    expect((await instanceState("research")).getByText("stopped")).toBeDefined()
  })

  it("shows the state the runtime observed, not the one the instance is meant to be in", async () => {
    const looping = { ...ada, process: "starting", detail: "restarting" } as const
    const failed = { ...unnamed, intended_state: "running", process: "failed" } as const
    openApp({ signedIn: true, instances: [looping, failed] })

    expect((await instanceState("Ada")).getByText("restarting")).toBeDefined()
    expect((await instanceState("research")).getByText("failed")).toBeDefined()
    expect(screen.queryByText("running")).toBeNull()
  })

  it("badges an instance whose setup is pending", async () => {
    const pending = { ...unnamed, setup_pending: true }
    openApp({ signedIn: true, instances: [ada, pending] })

    expect((await instanceState("research")).getByText("Setup pending")).toBeDefined()
    expect((await instanceState("research")).getByText("stopped")).toBeDefined()
    expect((await instanceState("Ada")).queryByText("Setup pending")).toBeNull()
  })

  it("draws each instance's avatar in its shape and palette color", async () => {
    const drawn = { ...ada, avatar: { shape: "squircle", color: "green" } } as const
    openApp({ signedIn: true, instances: [drawn, unnamed] })

    const avatar = (await instanceLink("Ada")).querySelector("[data-slot=avatar]")
    const fallback = avatar?.querySelector("[data-slot=avatar-fallback]")
    expect(avatar?.getAttribute("data-shape")).toBe("squircle")
    expect(fallback?.getAttribute("data-variant")).toBe("green")
    expect(fallback?.textContent).toBe("A")
    const other = (await instanceLink("research")).querySelector("[data-slot=avatar]")
    expect(other?.getAttribute("data-shape")).toBe("circle")
  })

  it("marks the instance the user selects and puts it in the URL", async () => {
    openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()

    await user.click(await instanceLink("Ada"))

    expect((await instanceLink("Ada")).getAttribute("aria-current")).toBe("page")
    expect((await instanceLink("research")).getAttribute("aria-current")).toBeNull()
    expect(window.location.pathname).toBe("/instances/hub-ada")
    expect(await screen.findByText("Nothing here yet")).toBeDefined()
  })

  it("restores the selection from the URL on a reload or an opened link", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect((await instanceLink("Ada")).getAttribute("aria-current")).toBe("page")
    expect(await screen.findByText("Nothing here yet")).toBeDefined()
  })

  it("opens a running instance's threads beside it, and follows the selected one in the main panel", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/threads/t1")

    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })

    expect((await instanceLink("Ada")).getAttribute("aria-current")).toBe("page")
    expect(await screen.findByRole("button", { name: "New thread" })).toBeDefined()
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
    expect(screen.getByRole("status", { name: "Loading the thread" })).toBeDefined()
    expect(screen.queryByText("Nothing here yet")).toBeNull()
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

    expect(await screen.findByText("Start it to see its memory.")).toBeDefined()
    expect(screen.queryByRole("tab", { name: "Knowledge graph" })).toBeNull()
    expect(hub.sockets).toHaveLength(1)
  })

  it("opens a running instance's stats from its page, and puts it in the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Stats" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/stats")
    expect(screen.getByRole("tab", { name: "Overview" })).toBeDefined()
    const relayed = hub.sockets.find(
      (socket) => socket.url === "ws://hub.test/instances/hub-ada/ws",
    )
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(relayed?.sent).toContainEqual(
      expect.objectContaining({ type: "call", method: "stats.get" }),
    )
  })

  it("asks to start a stopped instance to see its stats", async () => {
    window.history.replaceState(null, "", "/instances/hub-unnamed/stats")

    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText("Start it to see its stats.")).toBeDefined()
    expect(screen.queryByRole("tab", { name: "Overview" })).toBeNull()
    expect(hub.sockets).toHaveLength(1)
  })

  it("restores the config panel from the URL", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/config")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByRole("navigation", { name: "Config sections" })).toBeDefined()
    expect(screen.queryByText("Nothing here yet")).toBeNull()
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

    expect((await instanceLink("research")).getAttribute("aria-current")).toBe("page")
    expect(screen.queryByRole("button", { name: "New thread" })).toBeNull()
    expect(hub.sockets).toHaveLength(1)
  })

  it("selects nothing when the URL names an instance the hub does not have", async () => {
    window.history.replaceState(null, "", "/instances/hub-gone")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText("No instance selected")).toBeDefined()
    expect(screen.queryByText("Nothing here yet")).toBeNull()
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("aria-current")).toBeNull()
    }
  })

  it("lists the instances again once the connection is back", async () => {
    const { hub, clock } = openApp({ signedIn: true, instances: [ada] })
    expect(await instanceLink("Ada")).toBeDefined()

    act(() => hub.sockets[0]?.drop())
    hub.instances = [ada, unnamed]
    await act(() => clock.advance(16_000))

    expect(await instanceLink("research")).toBeDefined()
  })

  it("lists the instances again when the window regains focus, keeping the list meanwhile", async () => {
    const pending = { ...unnamed, setup_pending: true }
    const { hub } = openApp({ signedIn: true, instances: [pending] })
    expect((await instanceState("research")).getByText("Setup pending")).toBeDefined()

    // Signed in and started from another tab.
    hub.instances = [
      { ...pending, setup_pending: false, intended_state: "running", process: "running" },
    ]
    act(() => {
      window.dispatchEvent(new Event("focus"))
    })

    expect(screen.getByText("stopped")).toBeDefined()
    expect(await (await instanceState("research")).findByText("running")).toBeDefined()
    expect((await instanceState("research")).queryByText("Setup pending")).toBeNull()
  })

  it("lists the instances again every 30 seconds while the page is visible, and not while hidden", async () => {
    const { hub, clock } = openApp({ signedIn: true, instances: [unnamed] })
    const started = { ...unnamed, intended_state: "running", process: "running" } as const
    expect((await instanceState("research")).getByText("stopped")).toBeDefined()

    hub.instances = [started]
    await act(() => clock.advance(29_000))
    expect((await instanceState("research")).getByText("stopped")).toBeDefined()
    await act(() => clock.advance(1_000))
    expect((await instanceState("research")).getByText("running")).toBeDefined()

    showPage("hidden")
    hub.instances = [unnamed]
    await act(() => clock.advance(120_000))
    expect((await instanceState("research")).getByText("running")).toBeDefined()

    showPage("visible")
    await act(() => clock.advance(30_000))
    expect((await instanceState("research")).getByText("stopped")).toBeDefined()
  })

  it("says the hub has no instances yet", async () => {
    openApp({ signedIn: true })

    expect(await screen.findByText("No instances yet")).toBeDefined()
    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Usage",
      "New instance",
    ])
  })
})

describe("the usage page", () => {
  beforeEach(() => window.history.replaceState(null, "", "/"))

  it("opens from the top of the sidebar, above the instances, and reads on the hub", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada")
    const { hub } = openApp({ signedIn: true, instances: [ada, unnamed] })
    const user = userEvent.setup()
    const usage = await screen.findByRole("link", { name: "Usage" })

    expect(usage.compareDocumentPosition(await instanceLink("Ada"))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    await user.click(usage)

    expect(window.location.pathname).toBe("/usage")
    expect(await screen.findByRole("heading", { name: "Usage" })).toBeDefined()
    expect(usage.getAttribute("aria-current")).toBe("page")
    expect((await instanceLink("Ada")).getAttribute("aria-current")).toBeNull()
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    const onHub = hub.sockets.find((socket) => socket.url === "ws://hub.test/ws")
    expect(onHub?.sent).toContainEqual(
      expect.objectContaining({ type: "call", method: "stats.summary" }),
    )
  })

  it("restores the usage page from the URL", async () => {
    window.history.replaceState(null, "", "/usage")

    openApp({ signedIn: true, instances: [ada] })

    expect(await screen.findByRole("heading", { name: "Usage" })).toBeDefined()
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
    expect(await instanceLink("Ada")).toBeDefined()
    await user.click(await screen.findByRole("link", { name: "Usage" }))
    expect(await screen.findByRole("heading", { name: "Usage" })).toBeDefined()
    expect(screen.queryByRole("alert")).toBeNull()
  })
})

describe("creating an instance", () => {
  beforeEach(() => window.history.replaceState(null, "", "/"))

  it("opens the create wizard from the sidebar, at its package step", async () => {
    openApp({ signedIn: true, instances: [ada] })
    const user = userEvent.setup()

    await user.click(await instanceLink("Ada"))
    await user.click(await screen.findByRole("link", { name: "New instance" }))

    expect(await screen.findByRole("heading", { name: "New instance" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Prepare vanilla" })).toBeDefined()
    expect(window.location.pathname).toBe("/new")
    expect((await instanceLink("New instance")).getAttribute("aria-current")).toBe("page")
    expect((await instanceLink("Ada")).getAttribute("aria-current")).toBeNull()
  })
})
