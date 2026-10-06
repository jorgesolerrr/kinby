import type { InstanceSummary, OperationGetResult } from "@kinby/contract"
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
  const onRestored = vi.fn(() => Promise.resolve())
  render(
    <RemovedInstancesPage
      caller={caller}
      clock={clock}
      removed={removed}
      onRestored={onRestored}
    />,
  )
  return { caller, clock, onRestored, user: userEvent.setup() }
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
    const { caller, clock, onRestored, user } = openPage({
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
    expect(onRestored).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/removed")
    await act(() => clock.advance(1_000))

    expect(restores(caller)).toEqual([{ instance_id: "hub-grace" }])
    expect(onRestored).toHaveBeenCalledOnce()
    expect(window.location.pathname).toBe("/instances/hub-grace")
  })

  it("says on the row why a restoration failed, and offers Restore again", async () => {
    const detail = "The image sha256:image is gone. Rebuild the package's image to restore Grace."
    const { clock, onRestored, user } = openPage({
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
    expect(onRestored).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/removed")
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
