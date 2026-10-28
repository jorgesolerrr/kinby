import type { BrowserProfile, BrowserTakeoverStartCommand } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { BrowserProfiles } from "@/components/browser-profiles"

const linkedIn: BrowserProfile = { name: "LinkedIn", site: "www.linkedin.com" }

/** Ada's profiles on a hub that keeps `profiles`, and answers `answers` too. */
function renderProfiles(profiles: BrowserProfile[], answers: Answers = {}) {
  let kept = profiles
  const caller = {
    ...stubCaller({
      "browser.profile.list": () => ({ profiles: kept }),
      "browser.profile.rename": ({ name, new_name }) => {
        kept = kept.map((profile) =>
          profile.name === name ? { ...profile, name: new_name } : profile,
        )
        return { profiles: kept }
      },
      "browser.profile.delete": ({ name }) => {
        kept = kept.filter((profile) => profile.name !== name)
        return { profiles: kept }
      },
      ...answers,
    }),
    ...stubSubscriber(),
  }
  render(<BrowserProfiles caller={caller} clock={fakeClock()} instanceId="ada" />)
  return caller
}

async function listed(): Promise<HTMLElement> {
  return screen.findByRole("list", { name: "Browser profiles" })
}

describe("the instance's browser profiles", () => {
  it("lists each profile by name and site", async () => {
    renderProfiles([linkedIn, { name: "Indeed", site: "secure.indeed.com" }])

    const items = within(await listed()).getAllByRole("listitem")

    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("LinkedInwww.linkedin.com"),
      expect.stringContaining("Indeedsecure.indeed.com"),
    ])
  })

  it("renames a profile", async () => {
    const caller = renderProfiles([linkedIn])
    const user = userEvent.setup()

    await user.click(within(await listed()).getByRole("button", { name: "Rename LinkedIn" }))
    await user.type(screen.getByRole("textbox", { name: "New name" }), "Work")
    await user.click(screen.getByRole("button", { name: "Rename" }))

    expect(caller.calls).toContainEqual({
      method: "browser.profile.rename",
      params: { instance_id: "ada", name: "LinkedIn", new_name: "Work" },
    })
    await waitFor(() => expect(within(screen.getByRole("list")).getByText("Work")).toBeDefined())
  })

  it("deletes a profile once confirmed", async () => {
    const caller = renderProfiles([linkedIn])
    const user = userEvent.setup()

    await user.click(within(await listed()).getByRole("button", { name: "Delete LinkedIn" }))
    await user.click(screen.getByRole("button", { name: "Delete" }))

    expect(caller.calls).toContainEqual({
      method: "browser.profile.delete",
      params: { instance_id: "ada", name: "LinkedIn" },
    })
    await waitFor(() => expect(screen.queryByRole("list", { name: "Browser profiles" })).toBeNull())
  })

  it("opens a site with no turn for the user to sign in, and saves it as a profile", async () => {
    const caller = renderProfiles([], {
      "browser.takeover.input": () => ({}),
      "browser.takeover.finish": ({ profile }) => ({ name: profile, site: "www.linkedin.com" }),
    })
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "New profile" }))
    await user.type(screen.getByRole("textbox", { name: "Sign-in page" }), "https://linkedin.com")
    await user.click(screen.getByRole("button", { name: "Open" }))
    const [view] = caller.subscriptions
    if (view === undefined) throw new Error("The page did not start the live view.")
    await act(async () => {
      view.subscribed(0)
      view.deliver({ data: "/9j/frame" })
    })
    const page = screen.getByRole<HTMLImageElement>("img", { name: "The browser's page" })
    await user.type(screen.getByRole("textbox", { name: "Profile name" }), "LinkedIn")
    await user.click(screen.getByRole("button", { name: "Save profile" }))

    const {
      instance_id: instanceId,
      takeover_id: takeoverId,
      url,
    } = view.params as BrowserTakeoverStartCommand
    expect(view.method).toBe("browser.takeover.start")
    expect(instanceId).toBe("ada")
    expect(url).toBe("https://linkedin.com")
    expect(page.src).toBe("data:image/jpeg;base64,/9j/frame")
    expect(caller.calls.filter((call) => call.method.startsWith("browser.takeover"))).toEqual([
      {
        method: "browser.takeover.finish",
        params: { instance_id: "ada", takeover_id: takeoverId, profile: "LinkedIn" },
      },
    ])
    await waitFor(() => expect(view.cancelled).toBe(true))
    expect(caller.calls.filter((call) => call.method === "browser.profile.list")).toHaveLength(2)
  })

  it("closes the browser when the user cancels the sign-in", async () => {
    const caller = renderProfiles([])
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "New profile" }))
    await user.type(screen.getByRole("textbox", { name: "Sign-in page" }), "https://linkedin.com")
    await user.click(screen.getByRole("button", { name: "Open" }))
    await user.click(screen.getByRole("button", { name: "Cancel" }))

    expect(caller.subscriptions[0]?.cancelled).toBe(true)
    expect(screen.queryByRole("application", { name: "The browser" })).toBeNull()
  })
})
