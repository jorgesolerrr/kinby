import type { VaultEntry } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { Vault } from "@/components/vault"

const linkedIn: VaultEntry = { name: "LinkedIn", site: "https://www.linkedin.com" }

/** Ada's vault on a hub that keeps `entries`, by name and site only. */
function renderVault(entries: VaultEntry[]) {
  let kept = entries
  const caller = stubCaller({
    "browser.vault.list": () => ({ entries: kept }),
    "browser.vault.add": ({ name, site }) => {
      kept = [...kept, { name, site }]
      return { entries: kept }
    },
    "browser.vault.edit": ({ name, site }) => {
      kept = kept.map((entry) => (entry.name === name ? { name, site: site ?? entry.site } : entry))
      return { entries: kept }
    },
    "browser.vault.delete": ({ name }) => {
      kept = kept.filter((entry) => entry.name !== name)
      return { entries: kept }
    },
  })
  render(<Vault caller={caller} clock={fakeClock()} instanceId="ada" />)
  return caller
}

async function listed(): Promise<HTMLElement> {
  return screen.findByRole("list", { name: "Vault" })
}

describe("the instance's vault", () => {
  it("lists each entry by name and site", async () => {
    renderVault([linkedIn, { name: "Indeed", site: "https://secure.indeed.com" }])

    const items = within(await listed()).getAllByRole("listitem")

    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("LinkedInhttps://www.linkedin.com"),
      expect.stringContaining("Indeedhttps://secure.indeed.com"),
    ])
  })

  it("adds an entry with its sign-in", async () => {
    const caller = renderVault([])
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Add sign-in" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "LinkedIn")
    await user.type(screen.getByRole("textbox", { name: "Site" }), "https://www.linkedin.com")
    await user.type(screen.getByRole("textbox", { name: "Username" }), "ada@example.com")
    await user.type(screen.getByLabelText("Password"), "hunter2")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(caller.calls).toContainEqual({
      method: "browser.vault.add",
      params: {
        instance_id: "ada",
        name: "LinkedIn",
        site: "https://www.linkedin.com",
        username: "ada@example.com",
        password: "hunter2",
        totp_seed: null,
      },
    })
    expect(within(await listed()).getByRole("listitem", { name: "LinkedIn" })).toBeDefined()
  })

  it("edits an entry without showing a saved value, sending only what changed", async () => {
    const caller = renderVault([linkedIn])
    const user = userEvent.setup()

    await user.click(within(await listed()).getByRole("button", { name: "Edit LinkedIn" }))
    const username = screen.getByRole<HTMLInputElement>("textbox", { name: "Username" })
    const password = screen.getByLabelText<HTMLInputElement>("Password")
    const seed = screen.getByRole<HTMLInputElement>("textbox", { name: "TOTP seed" })
    const site = screen.getByRole<HTMLInputElement>("textbox", { name: "Site" })
    const shown = [site.value, username.value, password.value, seed.value]
    await user.type(password, "a new one")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(shown).toEqual([linkedIn.site, "", "", ""])
    expect(caller.calls).toContainEqual({
      method: "browser.vault.edit",
      params: {
        instance_id: "ada",
        name: "LinkedIn",
        site: "https://www.linkedin.com",
        username: null,
        password: "a new one",
        totp_seed: null,
      },
    })
  })

  it("deletes an entry once confirmed", async () => {
    const caller = renderVault([linkedIn])
    const user = userEvent.setup()

    await user.click(within(await listed()).getByRole("button", { name: "Delete LinkedIn" }))
    await user.click(screen.getByRole("button", { name: "Delete" }))

    expect(caller.calls).toContainEqual({
      method: "browser.vault.delete",
      params: { instance_id: "ada", name: "LinkedIn" },
    })
    await waitFor(() => expect(screen.queryByRole("list", { name: "Vault" })).toBeNull())
  })
})
