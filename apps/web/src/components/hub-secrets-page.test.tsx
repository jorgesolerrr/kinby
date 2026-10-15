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

/** Open the Replace dialog of the named hub secret, type a new value, and leave it unsaved. */
async function replacing(user: ReturnType<typeof userEvent.setup>, name: string) {
  const row = within(await screen.findByRole("listitem", { name }))
  await user.click(row.getByRole("button", { name: "Replace" }))
  const dialog = within(await screen.findByRole("dialog", { name: `Replace ${name}` }))
  await user.type(dialog.getByLabelText("New value"), "rotated-value")
  return dialog
}

const writes = (caller: ReturnType<typeof openPage>["caller"]) =>
  caller.calls.filter((call) => /^hub\.secret\.(set|delete)$/.test(call.method))

const listedNames = async () =>
  within(await screen.findByRole("list", { name: "Hub secrets" }))
    .getAllByRole("listitem")
    .map((item) => item.getAttribute("aria-label"))

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
  })

  it("adds a hub secret from its name and value, then lists it", async () => {
    const { caller, user } = openPage()

    await user.click(await screen.findByRole("button", { name: "Add a secret" }))
    const dialog = await screen.findByRole("dialog", { name: "Add a hub secret" })
    await user.type(within(dialog).getByLabelText("Name"), "CLAUDE_CODE_OAUTH_TOKEN")
    await user.type(within(dialog).getByLabelText("Value"), "oauth-value")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
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

  it("names the instances linked to each hub secret", async () => {
    openPage()

    const row = await screen.findByRole("listitem", { name: "GITHUB_TOKEN" })
    expect(within(row).getByText(/coder/)).toBeTruthy()
    expect(within(row).getByText(/reviewer/)).toBeTruthy()
  })

  it("replaces a hub secret no instance links", async () => {
    const { caller, user } = openPage()

    const dialog = await replacing(user, "OPENAI_API_KEY")
    expect(dialog.queryByRole("checkbox")).toBeNull()
    await user.click(dialog.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writes(caller)).toEqual([
      {
        method: "hub.secret.set",
        params: { name: "OPENAI_API_KEY", value: "rotated-value", recreate: [] },
      },
    ])
  })

  it("lists the linked instances on Replace and recreates the running ones", async () => {
    const { caller, user } = openPage()

    const dialog = await replacing(user, "GITHUB_TOKEN")
    expect(dialog.getByText(/coder/)).toBeTruthy()
    expect(dialog.getByText(/reviewer/)).toBeTruthy()
    const offer = dialog.getByRole("checkbox", { name: "Recreate the running ones now" })
    expect(offer.getAttribute("aria-checked")).toBe("true")
    await user.click(dialog.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writes(caller)).toEqual([
      {
        method: "hub.secret.set",
        params: { name: "GITHUB_TOKEN", value: "rotated-value", recreate: ["coder-id"] },
      },
    ])
  })

  it("recreates nothing once the offer is unticked", async () => {
    const { caller, user } = openPage()

    const dialog = await replacing(user, "GITHUB_TOKEN")
    await user.click(dialog.getByRole("checkbox", { name: "Recreate the running ones now" }))
    await user.click(dialog.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writes(caller)).toEqual([
      {
        method: "hub.secret.set",
        params: { name: "GITHUB_TOKEN", value: "rotated-value", recreate: [] },
      },
    ])
  })

  it("keeps the dialog open with why a set failed", async () => {
    const { user } = openPage({
      ...succeeded,
      state: "failed",
      detail: "no space left on device",
    })

    const dialog = await replacing(user, "GITHUB_TOKEN")
    await user.click(dialog.getByRole("button", { name: "Save" }))

    expect(await dialog.findByText("no space left on device")).toBeTruthy()
    expect(screen.getByRole("dialog", { name: "Replace GITHUB_TOKEN" })).toBeTruthy()
  })

  it("deletes a hub secret after a confirm that names the instances it unlinks", async () => {
    const { caller, user } = openPage()

    const row = within(await screen.findByRole("listitem", { name: "GITHUB_TOKEN" }))
    await user.click(row.getByRole("button", { name: "Delete" }))
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
