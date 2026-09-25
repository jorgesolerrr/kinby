import { createClient } from "@kinby/contract"
import { ACCESS_TOKEN, fakeHub } from "@kinby/contract/testing"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import App from "@/App"

function openApp({ signedIn }: { signedIn: boolean }) {
  const hub = fakeHub({ signedIn })
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
