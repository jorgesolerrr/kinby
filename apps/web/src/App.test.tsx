import { createClient } from "@kinby/contract"
import type { InstanceSummary } from "@kinby/contract"
import { ACCESS_TOKEN, fakeHub, instanceSummary } from "@kinby/contract/testing"
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
  render(<App client={createClient("http://hub.test", hub.transport)} />)
  return hub
}

const userMenu = () => screen.findByRole("button", { name: /You/ })

async function signIn(token: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText("Access token"), token)
  await user.click(screen.getByRole("button", { name: "Sign in" }))
}

describe("the app", () => {
  it("loads the shell once the hub accepts the access token", async () => {
    const hub = openApp({ signedIn: false })

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
    const hub = openApp({ signedIn: true })
    const user = userEvent.setup()

    await user.click(await userMenu())
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }))

    expect(await screen.findByLabelText("Access token")).toBeDefined()
    expect(hub.signedIn).toBe(false)
  })

  it("shows it is disconnected when the socket drops", async () => {
    const hub = openApp({ signedIn: true })
    await userMenu()

    act(() => hub.sockets[0]?.drop())

    expect(await screen.findByText("Disconnected")).toBeDefined()
    expect(await userMenu()).toBeDefined()
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
})

const instanceLink = (name: string) => screen.findByRole("link", { name })

async function instanceState(name: string) {
  const row = (await instanceLink(name)).closest("li")
  if (row === null) throw new Error(`${name} is not in a sidebar list`)
  return within(row)
}

describe("the instances", () => {
  beforeEach(() => window.history.replaceState(null, "", "/"))

  it("lists every instance in the sidebar with its name and state", async () => {
    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect((await instanceState("Ada")).getByText("running")).toBeDefined()
    expect((await instanceState("research")).getByText("stopped")).toBeDefined()
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

  it("selects nothing when the URL names an instance the hub does not have", async () => {
    window.history.replaceState(null, "", "/instances/hub-gone")

    openApp({ signedIn: true, instances: [ada, unnamed] })

    expect(await screen.findByText("No instance selected")).toBeDefined()
    expect(screen.queryByText("Nothing here yet")).toBeNull()
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("aria-current")).toBeNull()
    }
  })

  it("says the hub has no instances yet", async () => {
    openApp({ signedIn: true })

    expect(await screen.findByText("No instances yet")).toBeDefined()
    expect(screen.queryByRole("link")).toBeNull()
  })
})
