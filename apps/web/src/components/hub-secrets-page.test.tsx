import type { OperationGetResult } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
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

/** A page over hub secrets the stub keeps by name, which sets and deletes change. */
function openPage(outcome: OperationGetResult = succeeded) {
  let names = ["GITHUB_TOKEN", "OPENAI_API_KEY"]
  const listed = () => ({ secrets: names.map((name) => ({ name })) })
  const caller = stubCaller({
    "hub.secret.list": listed,
    "hub.secret.set": ({ name }) => {
      if (outcome.state === "succeeded" && !names.includes(name)) names = [...names, name].sort()
      return { operation_id: outcome.operation_id }
    },
    "operation.get": () => outcome,
    "hub.secret.delete": ({ name }) => {
      names = names.filter((each) => each !== name)
      return listed()
    },
  })
  render(<HubSecretsPage client={caller} clock={fakeClock()} />)
  return { caller, user: userEvent.setup() }
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
    const caller = stubCaller({ "hub.secret.list": () => ({ secrets: [] }) })
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

  it("replaces a hub secret's value", async () => {
    const { caller, user } = openPage()

    const row = within(await screen.findByRole("listitem", { name: "GITHUB_TOKEN" }))
    await user.click(row.getByRole("button", { name: "Replace" }))
    const dialog = await screen.findByRole("dialog", { name: "Replace GITHUB_TOKEN" })
    await user.type(within(dialog).getByLabelText("New value"), "rotated-value")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writes(caller)).toEqual([
      { method: "hub.secret.set", params: { name: "GITHUB_TOKEN", value: "rotated-value" } },
    ])
  })

  it("keeps the dialog open with why a set failed", async () => {
    const { user } = openPage({
      ...succeeded,
      state: "failed",
      detail: "no space left on device",
    })

    const row = within(await screen.findByRole("listitem", { name: "GITHUB_TOKEN" }))
    await user.click(row.getByRole("button", { name: "Replace" }))
    const dialog = await screen.findByRole("dialog", { name: "Replace GITHUB_TOKEN" })
    await user.type(within(dialog).getByLabelText("New value"), "rotated-value")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(await within(dialog).findByText("no space left on device")).toBeTruthy()
    expect(screen.getByRole("dialog", { name: "Replace GITHUB_TOKEN" })).toBeTruthy()
  })

  it("deletes a hub secret after a confirm", async () => {
    const { caller, user } = openPage()

    const row = within(await screen.findByRole("listitem", { name: "GITHUB_TOKEN" }))
    await user.click(row.getByRole("button", { name: "Delete" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Delete GITHUB_TOKEN?" })
    expect(writes(caller)).toEqual([])
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(async () => expect(await listedNames()).toEqual(["OPENAI_API_KEY"]))
    expect(writes(caller)).toEqual([
      { method: "hub.secret.delete", params: { name: "GITHUB_TOKEN" } },
    ])
  })
})
