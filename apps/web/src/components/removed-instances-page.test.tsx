import { CallError } from "@kinby/contract"
import type {
  InstanceSummary,
  OperationGetResult,
  OperationState,
  OperationStep,
} from "@kinby/contract"
import {
  type Answers,
  fakeClock,
  instanceSummary,
  type StubCaller,
  stubCaller,
} from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { RemovedInstancesPage } from "@/components/removed-instances-page"

const grace = instanceSummary({
  instance_id: "hub-grace",
  persona_name: "Grace",
  intended_state: "removed",
  process: "missing",
  avatar: { shape: "squircle", color: "green" },
  package: { id: "research", distribution: "kinby-packages", version: "1.2.0" },
})
const unnamed = instanceSummary({
  instance_id: "hub-unnamed",
  manifest_id: "scratch",
  intended_state: "removed",
  process: "missing",
  package: null,
})

function openPage(answers: Answers = {}, removed: InstanceSummary[] = [grace, unnamed]) {
  const caller = stubCaller(answers)
  const clock = fakeClock()
  const onChanged = vi.fn(() => Promise.resolve())
  const page = (listed: InstanceSummary[]) => (
    <RemovedInstancesPage caller={caller} clock={clock} removed={listed} onChanged={onChanged} />
  )
  const { rerender } = render(page(removed))
  // What the page shows once the removed instances are listed again as `listed`.
  const relist = (listed: InstanceSummary[]) => rerender(page(listed))
  return { caller, clock, onChanged, relist, user: userEvent.setup() }
}

function restoration(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-restore",
    instance_id: "hub-grace",
    kind: "restore",
    state: "running",
    detail: "",
    steps: [],
    ...fields,
  }
}

/** The hub answers each poll with the next operation, then keeps answering with the last. */
function polled(operations: OperationGetResult[]): () => OperationGetResult {
  return () => (operations.length > 1 ? operations.shift() : operations[0]) as OperationGetResult
}

const restores = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.restore").map((call) => call.params)

const preview = {
  instance_id: "hub-grace",
  directories: ["/srv/kinby/instances/hub-grace", "/srv/kinby/shared/grace-notes"],
  volumes: ["kinby-hub-grace-workspace"],
}

const previews = (caller: StubCaller) =>
  caller.calls
    .filter((call) => call.method === "instance.delete.preview")
    .map((call) => call.params)

const deletions = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.delete").map((call) => call.params)

function deletion(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-delete",
    instance_id: "hub-grace",
    kind: "delete",
    state: "running",
    detail: "",
    steps: [],
    ...fields,
  }
}

function step(name: string, state: OperationState, detail: string): OperationStep {
  return { name, state, detail }
}

/** Open the instance's deletion dialog, wait for its preview, type the name, and confirm. */
async function confirmDeletion(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(within(rowOf(name)).getByRole("button", { name: "Delete permanently" }))
  const dialog = within(
    await screen.findByRole("alertdialog", { name: `Delete ${name} permanently` }),
  )
  await dialog.findByRole("list", { name: "Directories" })
  await user.type(dialog.getByRole("textbox", { name: `Type ${name} to confirm` }), name)
  await user.click(dialog.getByRole("button", { name: "Delete permanently" }))
  return dialog
}

function rowOf(name: string) {
  const row = screen.getAllByRole("listitem").find((item) => within(item).queryByText(name))
  if (row === undefined) throw new Error(`No row for ${name}`)
  return row
}

describe("the Removed instances page", () => {
  beforeEach(() => window.history.replaceState(null, "", "/removed"))

  it("lists each removed instance with its avatar, its persona name, and its package", () => {
    openPage()

    const row = rowOf("Grace")
    const avatar = row.querySelector("[data-slot=avatar]")
    expect(avatar?.getAttribute("data-shape")).toBe("squircle")
    expect(avatar?.querySelector("[data-slot=avatar-fallback]")?.textContent).toBe("G")
    expect(within(row).getByText("Package: research from kinby-packages")).toBeDefined()
    expect(
      within(rowOf("scratch")).getByText("Package: None, kinby's built-in defaults"),
    ).toBeDefined()
  })

  it("restores an instance, showing its progress on the row, then opens it once listed again", async () => {
    const { caller, clock, onChanged, user } = openPage({
      "instance.restore": () => ({ operation_id: "op-restore", instance_id: "hub-grace" }),
      "operation.get": polled([
        restoration({ state: "running" }),
        restoration({ state: "succeeded" }),
      ]),
    })

    await user.click(within(rowOf("Grace")).getByRole("button", { name: "Restore" }))
    await act(() => clock.advance(0))

    const restore = within(rowOf("Grace")).getByRole("button", { name: /Restore/ })
    expect(restore.hasAttribute("disabled")).toBe(true)
    expect(within(restore).getByRole("status", { name: "Loading" })).toBeDefined()
    expect(within(rowOf("scratch")).queryByRole("status")).toBeNull()
    expect(onChanged).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/removed")
    await act(() => clock.advance(1_000))

    expect(restores(caller)).toEqual([{ instance_id: "hub-grace" }])
    expect(onChanged).toHaveBeenCalledOnce()
    expect(window.location.pathname).toBe("/instances/hub-grace")
  })

  it("keeps a row being restored when the lists drop it first, and still opens it", async () => {
    const { clock, onChanged, relist, user } = openPage({
      "instance.restore": () => ({ operation_id: "op-restore", instance_id: "hub-grace" }),
      "operation.get": polled([
        restoration({ state: "running" }),
        restoration({ state: "succeeded" }),
      ]),
    })

    await user.click(within(rowOf("Grace")).getByRole("button", { name: "Restore" }))
    await act(() => clock.advance(0))
    relist([unnamed])
    expect(within(rowOf("Grace")).getByRole("button", { name: /Restore/ })).toBeDefined()
    await act(() => clock.advance(1_000))

    expect(onChanged).toHaveBeenCalledOnce()
    expect(window.location.pathname).toBe("/instances/hub-grace")
  })

  it("says on the row why a restoration failed, and offers Restore again", async () => {
    const detail = "The image sha256:image is gone. Rebuild the package's image to restore Grace."
    const { clock, onChanged, user } = openPage({
      "instance.restore": () => ({ operation_id: "op-restore", instance_id: "hub-grace" }),
      "operation.get": () => restoration({ state: "failed", detail }),
    })

    await user.click(within(rowOf("Grace")).getByRole("button", { name: "Restore" }))
    await act(() => clock.advance(0))

    const alert = within(within(rowOf("Grace")).getByRole("alert"))
    expect(alert.getByText("The restoration failed")).toBeDefined()
    expect(alert.getByText(detail)).toBeDefined()
    expect(within(rowOf("scratch")).queryByRole("alert")).toBeNull()
    const restore = within(rowOf("Grace")).getByRole("button", { name: "Restore" })
    expect(restore.hasAttribute("disabled")).toBe(false)
    expect(onChanged).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/removed")
  })

  it("previews a deletion and lists every directory and volume the hub would delete", async () => {
    const { caller, user } = openPage({ "instance.delete.preview": () => preview })

    await user.click(within(rowOf("Grace")).getByRole("button", { name: "Delete permanently" }))

    const dialog = within(
      await screen.findByRole("alertdialog", { name: "Delete Grace permanently" }),
    )
    expect(dialog.getByText(/can't be undone/)).toBeDefined()
    expect(await dialog.findByText("/srv/kinby/instances/hub-grace")).toBeDefined()
    expect(dialog.getByText("/srv/kinby/shared/grace-notes")).toBeDefined()
    expect(dialog.getByText("kinby-hub-grace-workspace")).toBeDefined()
    expect(previews(caller)).toEqual([{ instance_id: "hub-grace" }])
  })

  it("says why when the hub does not preview a deletion, and offers nothing to confirm", async () => {
    const { user } = openPage({
      "instance.delete.preview": () => {
        throw new CallError({
          code: "NOT_FOUND",
          message: "Instance hub-grace is not removed.",
          retryable: false,
        })
      },
    })

    await user.click(within(rowOf("Grace")).getByRole("button", { name: "Delete permanently" }))
    const dialog = within(
      await screen.findByRole("alertdialog", { name: "Delete Grace permanently" }),
    )

    const alert = within(await dialog.findByRole("alert"))
    expect(alert.getByText("The hub did not preview the deletion")).toBeDefined()
    expect(alert.getByText("Instance hub-grace is not removed.")).toBeDefined()
    await user.type(dialog.getByRole("textbox", { name: "Type Grace to confirm" }), "Grace")
    expect(
      dialog.getByRole("button", { name: "Delete permanently" }).hasAttribute("disabled"),
    ).toBe(true)
  })

  it("enables the deletion only once the name is typed exactly", async () => {
    const { caller, user } = openPage({
      "instance.delete.preview": ({ instance_id }) => ({ ...preview, instance_id }),
    })

    await user.click(within(rowOf("scratch")).getByRole("button", { name: "Delete permanently" }))

    const dialog = within(
      await screen.findByRole("alertdialog", { name: "Delete scratch permanently" }),
    )
    const confirm = dialog.getByRole("button", { name: "Delete permanently" })
    const typed = dialog.getByRole("textbox", { name: "Type scratch to confirm" })
    expect(confirm.hasAttribute("disabled")).toBe(true)
    await user.type(typed, "Scratch")
    expect(confirm.hasAttribute("disabled")).toBe(true)
    await user.clear(typed)
    await user.type(typed, "scratch")
    expect(confirm.hasAttribute("disabled")).toBe(false)
    expect(deletions(caller)).toEqual([])
  })

  it("deletes the previewed targets, showing each one's step, then lists the instances again", async () => {
    const running = [
      step("validate", "succeeded", "Revalidating the retained inventory against the preview."),
      step("directory /srv/kinby/instances/hub-grace", "running", "Deleting it."),
    ]
    const { caller, clock, onChanged, user } = openPage({
      "instance.delete.preview": () => preview,
      "instance.delete": () => ({ operation_id: "op-delete", instance_id: "hub-grace" }),
      "operation.get": polled([
        deletion({ state: "running", steps: running }),
        deletion({ state: "succeeded" }),
      ]),
    })

    const dialog = await confirmDeletion(user, "Grace")
    await act(() => clock.advance(0))

    const steps = dialog.getByRole("list", { name: "Deletion steps" })
    const target = within(steps).getByRole("listitem", {
      name: "directory /srv/kinby/instances/hub-grace",
    })
    expect(target.textContent).toContain("Running")
    expect(
      dialog.getByRole("button", { name: "Delete permanently" }).hasAttribute("disabled"),
    ).toBe(true)
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(deletions(caller)).toEqual([
      {
        instance_id: "hub-grace",
        directories: ["/srv/kinby/instances/hub-grace", "/srv/kinby/shared/grace-notes"],
        volumes: ["kinby-hub-grace-workspace"],
      },
    ])
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("previews again when the hub refuses because the targets changed, and clears the name", async () => {
    const moved = { ...preview, directories: ["/mnt/elsewhere/hub-grace"] }
    const previewed = [preview, moved]
    const { caller, clock, onChanged, user } = openPage({
      "instance.delete.preview": () => previewed.shift() ?? moved,
      "instance.delete": () => ({ operation_id: "op-delete", instance_id: "hub-grace" }),
      "operation.get": () =>
        deletion({
          state: "failed",
          detail:
            "The deletion targets changed since the preview, so nothing was deleted. " +
            "Preview the deletion again.",
          steps: [step("validate", "failed", "Revalidating the retained inventory.")],
        }),
    })

    const dialog = await confirmDeletion(user, "Grace")
    await act(() => clock.advance(0))

    expect(await dialog.findByText("What would be deleted changed")).toBeDefined()
    expect(dialog.getByText("/mnt/elsewhere/hub-grace")).toBeDefined()
    expect(dialog.queryByText("/srv/kinby/instances/hub-grace")).toBeNull()
    const typed = dialog.getByRole("textbox", { name: "Type Grace to confirm" })
    expect((typed as HTMLInputElement).value).toBe("")
    const confirm = dialog.getByRole("button", { name: "Delete permanently" })
    expect(confirm.hasAttribute("disabled")).toBe(true)
    expect(dialog.queryByRole("alert", { name: "The deletion failed" })).toBeNull()
    expect(previews(caller)).toHaveLength(2)

    await user.type(typed, "Grace")
    await user.click(confirm)

    expect(deletions(caller)).toEqual([
      {
        instance_id: "hub-grace",
        directories: preview.directories,
        volumes: preview.volumes,
      },
      { instance_id: "hub-grace", directories: moved.directories, volumes: moved.volumes },
    ])
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("keeps the row after a deletion fails partway, and starts again from a fresh preview", async () => {
    const detail = 'Named volume "kinby-hub-grace-workspace" is in use.'
    const left = { ...preview, directories: [] }
    const previewed = [preview, left]
    const { caller, clock, onChanged, user } = openPage({
      "instance.delete.preview": () => previewed.shift() ?? left,
      "instance.delete": () => ({ operation_id: "op-delete", instance_id: "hub-grace" }),
      "operation.get": () =>
        deletion({
          state: "failed",
          detail,
          steps: [
            step("validate", "succeeded", "Revalidating the retained inventory."),
            step("directory /srv/kinby/instances/hub-grace", "succeeded", "Deleting it."),
            step("directory /srv/kinby/shared/grace-notes", "succeeded", "Deleting it."),
            step("volume kinby-hub-grace-workspace", "failed", detail),
          ],
        }),
    })

    const dialog = await confirmDeletion(user, "Grace")
    await act(() => clock.advance(0))

    const steps = dialog.getByRole("list", { name: "Deletion steps" })
    const volume = within(steps).getByRole("listitem", { name: "volume kinby-hub-grace-workspace" })
    expect(volume.textContent).toContain("Failed")
    const alert = within(dialog.getByRole("alert"))
    expect(alert.getByText("The deletion failed")).toBeDefined()
    expect(alert.getByText(detail)).toBeDefined()
    expect(
      dialog.getByRole("button", { name: "Delete permanently" }).hasAttribute("disabled"),
    ).toBe(true)
    expect(onChanged).not.toHaveBeenCalled()
    expect(previews(caller)).toHaveLength(1)

    await user.click(dialog.getByRole("button", { name: "Cancel" }))
    await user.click(within(rowOf("Grace")).getByRole("button", { name: "Delete permanently" }))

    const again = within(
      await screen.findByRole("alertdialog", { name: "Delete Grace permanently" }),
    )
    expect(await again.findByText("kinby-hub-grace-workspace")).toBeDefined()
    expect(again.queryByRole("list", { name: "Directories" })).toBeNull()
    expect(again.queryByRole("list", { name: "Deletion steps" })).toBeNull()
    expect(again.queryByRole("alert")).toBeNull()
    expect((again.getByRole("textbox") as HTMLInputElement).value).toBe("")
    expect(previews(caller)).toHaveLength(2)
  })

  it("shows an empty state with a link home when no instance is removed", async () => {
    const { user } = openPage({}, [])

    expect(screen.getByText("No removed instances")).toBeDefined()
    expect(screen.queryByRole("listitem")).toBeNull()
    const home = screen.getByRole("link", { name: "Go home" })
    expect(home.getAttribute("href")).toBe("/")
    await user.click(home)

    expect(window.location.pathname).toBe("/")
  })
})
