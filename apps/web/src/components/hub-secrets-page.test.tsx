import type { HubSecretLink, OperationGetResult } from "@kinby/contract"
import { fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { HubSecretsPage } from "@/components/hub-secrets-page"

const succeeded: OperationGetResult = {
  operation_id: "operation-1",
  instance_id: null,
  kind: "hub-secret",
  state: "succeeded",
  detail: "Hub secret saved.",
  steps: [],
}

const coder = instanceSummary({ instance_id: "coder-id", manifest_id: "coder" })
const reviewer = instanceSummary({
  instance_id: "reviewer-id",
  manifest_id: "reviewer",
  intended_state: "stopped",
  process: "stopped",
})

/**
 * A page over hub secrets the stub keeps by name, which sets and deletes change. The running coder
 * and the stopped reviewer link GITHUB_TOKEN.
 */
function openPage(outcome: OperationGetResult = succeeded) {
  let names = ["GITHUB_TOKEN", "OPENAI_API_KEY"]
  const links = (name: string): HubSecretLink[] =>
    name === "GITHUB_TOKEN"
      ? [coder, reviewer].map(({ instance_id, manifest_id }) => ({
          instance_id,
          manifest_id,
          field: "GITHUB_TOKEN",
        }))
      : []
  const caller = stubCaller({
    "hub.secret.list": () => ({ secrets: names.map((name) => ({ name, links: links(name) })) }),
    "instance.list": () => ({ instances: [coder, reviewer] }),
    "hub.secret.set": ({ name }) => {
      if (outcome.state === "succeeded" && !names.includes(name)) names = [...names, name].sort()
      return { operation_id: outcome.operation_id }
    },
    "operation.get": () => outcome,
    "hub.secret.delete": ({ name }) => {
      names = names.filter((each) => each !== name)
      return { unlinked: links(name) }
    },
  })
  render(<HubSecretsPage client={caller} clock={fakeClock()} />)
  return { caller, user: userEvent.setup() }
}

/** Open Replace under the named hub secret's row, type a new value, and leave it unsaved. */
async function replacing(user: ReturnType<typeof userEvent.setup>, name: string) {
  const row = within(await screen.findByRole("row", { name }))
  await user.click(row.getByRole("button", { name: "Replace" }))
  const form = within(await screen.findByRole("form", { name: `Replace ${name}` }))
  await user.type(form.getByLabelText("New value"), "rotated-value")
  return form
}

const replaceForm = () => screen.queryByRole("form", { name: /^Replace / })

const writes = (caller: ReturnType<typeof openPage>["caller"]) =>
  caller.calls.filter((call) => /^hub\.secret\.(set|delete)$/.test(call.method))

const listedNames = async () =>
  within(await screen.findByRole("table", { name: "Hub secrets" }))
    .getAllByRole("row")
    .map((row) => row.getAttribute("aria-label"))
    .filter((name) => name !== null)

const nameField = async () =>
  (await screen.findByRole("textbox", { name: "Name" })) as HTMLInputElement
const valueField = () => screen.getByLabelText("Value") as HTMLInputElement

describe("HubSecretsPage", () => {
  it("lists the hub secrets by name", async () => {
    openPage()

    expect(await listedNames()).toEqual(["GITHUB_TOKEN", "OPENAI_API_KEY"])
  })

  it("says when the hub keeps no secret", async () => {
    const caller = stubCaller({
      "hub.secret.list": () => ({ secrets: [] }),
      "instance.list": () => ({ instances: [] }),
    })
    render(<HubSecretsPage client={caller} clock={fakeClock()} />)

    expect(await screen.findByText("The hub keeps no secrets yet.")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Add secret" })).toBeTruthy()
  })

  it("adds a hub secret from the table's first row, then lists it and empties the row", async () => {
    const { caller, user } = openPage()

    await user.type(await nameField(), "CLAUDE_CODE_OAUTH_TOKEN")
    await user.type(valueField(), "oauth-value")
    await user.click(screen.getByRole("button", { name: "Add secret" }))

    await waitFor(async () => expect((await nameField()).value).toBe(""))
    expect(valueField().value).toBe("")
    expect(await listedNames()).toEqual([
      "CLAUDE_CODE_OAUTH_TOKEN",
      "GITHUB_TOKEN",
      "OPENAI_API_KEY",
    ])
    expect(writes(caller)).toEqual([
      {
        method: "hub.secret.set",
        params: { name: "CLAUDE_CODE_OAUTH_TOKEN", value: "oauth-value" },
      },
    ])
  })

  it("adds nothing until the row has a name and a value", async () => {
    const { caller, user } = openPage()

    await user.type(await nameField(), "NEW_TOKEN")

    expect(screen.getByRole("button", { name: "Add secret" }).hasAttribute("disabled")).toBe(true)
    expect(writes(caller)).toEqual([])
  })

  it("keeps the add row with why an add failed", async () => {
    const { user } = openPage({ ...succeeded, state: "failed", detail: "no space left on device" })

    await user.type(await nameField(), "NEW_TOKEN")
    await user.type(valueField(), "value")
    await user.click(screen.getByRole("button", { name: "Add secret" }))

    expect(await screen.findByText("no space left on device")).toBeTruthy()
    expect((await nameField()).value).toBe("NEW_TOKEN")
  })

  it("names the instances linked to each hub secret with the field, or that none is", async () => {
    openPage()

    const row = within(await screen.findByRole("row", { name: "GITHUB_TOKEN" }))
    expect(row.getByText("coder")).toBeTruthy()
    expect(row.getByText("reviewer")).toBeTruthy()
    // The name, then each link's field.
    expect(row.getAllByText("GITHUB_TOKEN")).toHaveLength(3)
    const unlinked = within(screen.getByRole("row", { name: "OPENAI_API_KEY" }))
    expect(unlinked.getByText("Not linked")).toBeTruthy()
  })

  it("replaces a hub secret no instance links", async () => {
    const { caller, user } = openPage()

    const form = await replacing(user, "OPENAI_API_KEY")
    expect(form.queryByRole("checkbox")).toBeNull()
    await user.click(form.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(replaceForm()).toBeNull())
    expect(writes(caller)).toEqual([
      {
        method: "hub.secret.set",
        params: { name: "OPENAI_API_KEY", value: "rotated-value", recreate: [] },
      },
    ])
  })

  it("replaces in place under the row and recreates the running linked ones", async () => {
    const { caller, user } = openPage()

    const form = await replacing(user, "GITHUB_TOKEN")
    const row = within(screen.getByRole("row", { name: "GITHUB_TOKEN" }))
    expect(row.getByRole("button", { name: "Replace" }).getAttribute("aria-expanded")).toBe("true")
    const offer = form.getByRole("checkbox", { name: "Recreate the running ones now" })
    expect(offer.getAttribute("aria-checked")).toBe("true")
    await user.click(form.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(replaceForm()).toBeNull())
    expect(writes(caller)).toEqual([
      {
        method: "hub.secret.set",
        params: { name: "GITHUB_TOKEN", value: "rotated-value", recreate: ["coder-id"] },
      },
    ])
  })

  it("recreates nothing once the offer is unticked", async () => {
    const { caller, user } = openPage()

    const form = await replacing(user, "GITHUB_TOKEN")
    await user.click(form.getByRole("checkbox", { name: "Recreate the running ones now" }))
    await user.click(form.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(replaceForm()).toBeNull())
    expect(writes(caller)).toEqual([
      {
        method: "hub.secret.set",
        params: { name: "GITHUB_TOKEN", value: "rotated-value", recreate: [] },
      },
    ])
  })

  it("closes Replace on Cancel, saving nothing", async () => {
    const { caller, user } = openPage()

    const form = await replacing(user, "GITHUB_TOKEN")
    await user.click(form.getByRole("button", { name: "Cancel" }))

    expect(replaceForm()).toBeNull()
    expect(writes(caller)).toEqual([])
  })

  it("keeps Replace open with why a set failed", async () => {
    const { user } = openPage({
      ...succeeded,
      state: "failed",
      detail: "no space left on device",
    })

    const form = await replacing(user, "GITHUB_TOKEN")
    await user.click(form.getByRole("button", { name: "Save" }))

    expect(await form.findByText("no space left on device")).toBeTruthy()
    expect(replaceForm()).toBeTruthy()
  })

  it("deletes a hub secret after a confirm that names the instances it unlinks", async () => {
    const { caller, user } = openPage()

    const row = within(await screen.findByRole("row", { name: "GITHUB_TOKEN" }))
    await user.click(row.getByRole("button", { name: "Delete GITHUB_TOKEN" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Delete GITHUB_TOKEN?" })
    expect(within(dialog).getByText(/coder/)).toBeTruthy()
    expect(within(dialog).getByText(/reviewer/)).toBeTruthy()
    expect(writes(caller)).toEqual([])
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(async () => expect(await listedNames()).toEqual(["OPENAI_API_KEY"]))
    expect(writes(caller)).toEqual([
      { method: "hub.secret.delete", params: { name: "GITHUB_TOKEN" } },
    ])
  })
})
