import { CallError } from "@kinby/contract"
import type { HubSecretLink, OperationGetResult } from "@kinby/contract"
import type { Answers } from "@kinby/contract/testing"
import { fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { HubSecretsSection } from "@/components/hub-secrets-section"

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
 * A section over hub secrets the stub keeps by name, which sets and deletes change. The running
 * coder and the stopped reviewer link GITHUB_TOKEN.
 */
function openPage(outcome: OperationGetResult = succeeded, answers: Answers = {}) {
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
    ...answers,
  })
  render(<HubSecretsSection client={caller} clock={fakeClock()} />)
  return { caller, user: userEvent.setup() }
}

type User = ReturnType<typeof userEvent.setup>

const secretRow = async (name: string) => within(await screen.findByRole("listitem", { name }))

/** The header's Add secret, which comes before the add form's. */
const headerAdd = () => screen.getAllByRole("button", { name: "Add secret" })[0]

const addForm = () => screen.queryByRole("form", { name: "Add a secret" })

/** Open the add form from the section's header. */
async function adding(user: User) {
  await screen.findByRole("list", { name: "Hub secrets" })
  await user.click(headerAdd())
  return within(await screen.findByRole("form", { name: "Add a secret" }))
}

/** Open Replace under the named hub secret's row, type a new value, and leave it unsaved. */
async function replacing(user: User, name: string) {
  const row = await secretRow(name)
  await user.click(row.getByRole("button", { name: "Replace" }))
  const form = within(row.getByRole("form", { name: `Replace ${name}` }))
  await user.type(form.getByLabelText("New value"), "rotated-value")
  return form
}

const replaceForm = () => screen.queryByRole("form", { name: /^Replace / })

/** Ask to delete the named hub secret from its row's menu, and return the confirmation. */
async function askingToDelete(user: User, name: string) {
  const row = await secretRow(name)
  await user.click(row.getByRole("button", { name: `More for ${name}` }))
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }))
  return within(await screen.findByRole("alertdialog", { name: `Delete ${name}?` }))
}

const writes = (caller: ReturnType<typeof openPage>["caller"]) =>
  caller.calls.filter((call) => /^hub\.secret\.(set|delete)$/.test(call.method))

const listedNames = async () =>
  within(await screen.findByRole("list", { name: "Hub secrets" }))
    .getAllByRole("listitem")
    .map((row) => row.getAttribute("aria-label"))

describe("HubSecretsSection", () => {
  it("lists the hub secrets by name", async () => {
    openPage()

    expect(await listedNames()).toEqual(["GITHUB_TOKEN", "OPENAI_API_KEY"])
  })

  it("says what hub secrets are for when the hub keeps none, and points at Add secret", async () => {
    const caller = stubCaller({
      "hub.secret.list": () => ({ secrets: [] }),
      "instance.list": () => ({ instances: [] }),
    })
    render(<HubSecretsSection client={caller} clock={fakeClock()} />)

    expect(await screen.findByText("No hub secrets yet")).toBeTruthy()
    expect(screen.getByText(/once with Add secret/)).toBeTruthy()
    expect(headerAdd().hasAttribute("disabled")).toBe(false)
  })

  it("names the instances linked to each hub secret with the field on one line, or that none is", async () => {
    openPage()

    const linked = (await secretRow("GITHUB_TOKEN")).getByText(
      "coder · GITHUB_TOKEN, reviewer · GITHUB_TOKEN",
    )
    expect(linked.getAttribute("title")).toBe("coder · GITHUB_TOKEN, reviewer · GITHUB_TOKEN")
    expect((await secretRow("OPENAI_API_KEY")).getByText("Not linked")).toBeTruthy()
  })

  it("adds a hub secret from a form the header opens at the top, then lists it and closes", async () => {
    const { caller, user } = openPage()

    const form = await adding(user)
    expect(headerAdd().hasAttribute("disabled")).toBe(true)
    expect(document.activeElement).toBe(form.getByLabelText("Name"))
    await user.type(form.getByLabelText("Name"), "CLAUDE_CODE_OAUTH_TOKEN")
    await user.type(form.getByLabelText("Value"), "oauth-value")
    await user.click(form.getByRole("button", { name: "Add secret" }))

    await waitFor(() => expect(addForm()).toBeNull())
    expect(headerAdd().hasAttribute("disabled")).toBe(false)
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

  it("adds nothing until the form has a name and a value", async () => {
    const { caller, user } = openPage()

    const form = await adding(user)
    await user.type(form.getByLabelText("Name"), "NEW_TOKEN")

    expect(form.getByRole("button", { name: "Add secret" }).hasAttribute("disabled")).toBe(true)
    expect(writes(caller)).toEqual([])
  })

  it("closes the add form on Cancel, adding nothing", async () => {
    const { caller, user } = openPage()

    const form = await adding(user)
    await user.type(form.getByLabelText("Name"), "NEW_TOKEN")
    await user.click(form.getByRole("button", { name: "Cancel" }))

    expect(addForm()).toBeNull()
    expect(headerAdd().hasAttribute("disabled")).toBe(false)
    expect(writes(caller)).toEqual([])
  })

  it("keeps the add form with why an add failed", async () => {
    const { user } = openPage({ ...succeeded, state: "failed", detail: "no space left on device" })

    const form = await adding(user)
    await user.type(form.getByLabelText("Name"), "NEW_TOKEN")
    await user.type(form.getByLabelText("Value"), "value")
    await user.click(form.getByRole("button", { name: "Add secret" }))

    expect(await form.findByText("no space left on device")).toBeTruthy()
    expect((form.getByLabelText("Name") as HTMLInputElement).value).toBe("NEW_TOKEN")
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

  it("replaces under the row, value focused, and recreates the running linked ones by name", async () => {
    const { caller, user } = openPage()

    const row = await secretRow("GITHUB_TOKEN")
    await user.click(row.getByRole("button", { name: "Replace" }))
    const form = within(row.getByRole("form", { name: "Replace GITHUB_TOKEN" }))
    expect(document.activeElement).toBe(form.getByLabelText("New value"))
    expect(row.getByRole("button", { name: "Replace" }).getAttribute("aria-expanded")).toBe("true")
    const offer = form.getByRole("checkbox", { name: "Recreate coder now" })
    expect(offer.getAttribute("aria-checked")).toBe("true")
    await user.type(form.getByLabelText("New value"), "rotated-value")
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
    await user.click(form.getByRole("checkbox", { name: "Recreate coder now" }))
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

  it("sends a replace once while the hub reads the secrets again", async () => {
    const { caller, user } = openPage({ ...succeeded, state: "running" })

    const form = await replacing(user, "GITHUB_TOKEN")
    await user.click(form.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(writes(caller)).toHaveLength(1))
    const reads = caller.calls.filter((call) => call.method === "hub.secret.list").length
    act(() => {
      window.dispatchEvent(new Event("focus"))
    })

    await waitFor(() =>
      expect(caller.calls.filter((call) => call.method === "hub.secret.list")).toHaveLength(
        reads + 1,
      ),
    )
    expect(writes(caller)).toHaveLength(1)
  })

  it("keeps one form open at a time", async () => {
    const { user } = openPage()

    await replacing(user, "GITHUB_TOKEN")
    await replacing(user, "OPENAI_API_KEY")
    expect(screen.getAllByRole("form").map((form) => form.getAttribute("aria-label"))).toEqual([
      "Replace OPENAI_API_KEY",
    ])

    await user.click(headerAdd())
    expect(screen.getAllByRole("form").map((form) => form.getAttribute("aria-label"))).toEqual([
      "Add a secret",
    ])

    await replacing(user, "GITHUB_TOKEN")
    expect(screen.getAllByRole("form").map((form) => form.getAttribute("aria-label"))).toEqual([
      "Replace GITHUB_TOKEN",
    ])
  })

  it("deletes a hub secret from its menu after a confirm that names the instances it unlinks", async () => {
    const { caller, user } = openPage()

    const dialog = await askingToDelete(user, "GITHUB_TOKEN")
    expect(dialog.getByText(/coder · GITHUB_TOKEN, reviewer · GITHUB_TOKEN/)).toBeTruthy()
    expect(writes(caller)).toEqual([])
    await user.click(dialog.getByRole("button", { name: "Delete" }))

    await waitFor(async () => expect(await listedNames()).toEqual(["OPENAI_API_KEY"]))
    expect(writes(caller)).toEqual([
      { method: "hub.secret.delete", params: { name: "GITHUB_TOKEN" } },
    ])
  })

  it("shows why a delete failed on the secret's row, and keeps it", async () => {
    const { user } = openPage(succeeded, {
      "hub.secret.delete": () => {
        throw new CallError({ code: "INTERNAL", message: "disk is read-only", retryable: false })
      },
    })

    const dialog = await askingToDelete(user, "GITHUB_TOKEN")
    await user.click(dialog.getByRole("button", { name: "Delete" }))

    const row = await secretRow("GITHUB_TOKEN")
    expect(await row.findByText("Could not delete GITHUB_TOKEN")).toBeTruthy()
    expect(row.getByText("disk is read-only")).toBeTruthy()
    expect(await listedNames()).toEqual(["GITHUB_TOKEN", "OPENAI_API_KEY"])
  })
})
