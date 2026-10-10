import type { InstanceSummary, OperationGetResult, OperationStep } from "@kinby/contract"
import { type Answers, fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { CoreSection } from "@/components/core-section"

const HUB = "b".repeat(40)

const behind = instanceSummary({
  instance_id: "hub-ada",
  source_revision: "a".repeat(40),
  notices: [
    {
      code: "revision_behind",
      message: "The instance runs kinby aaaaaaa, and the hub is at bbbbbbb.",
      instance_revision: "a".repeat(40),
      hub_revision: HUB,
    },
  ],
})

const step = (name: string, state: OperationStep["state"], detail = ""): OperationStep => ({
  name,
  state,
  detail,
})

function operation(
  state: OperationGetResult["state"],
  steps: OperationStep[],
  detail = "",
): OperationGetResult {
  return { operation_id: "op-1", instance_id: "hub-ada", kind: "update", state, detail, steps }
}

function openSection(instance: InstanceSummary, answers: Answers = {}) {
  const caller = stubCaller({
    "instance.update": ({ instance_id }) => ({ operation_id: "op-1", instance_id }),
    ...answers,
  })
  const clock = fakeClock()
  const onChanged = vi.fn()
  render(<CoreSection caller={caller} clock={clock} instance={instance} onChanged={onChanged} />)
  return { caller, clock, onChanged, user: userEvent.setup() }
}

const updates = (caller: ReturnType<typeof openSection>["caller"]) =>
  caller.calls.filter((call) => call.method === "instance.update").map((call) => call.params)

describe("CoreSection", () => {
  it("shows the kinby revision and the notice that the hub is ahead", () => {
    openSection(behind)

    const facts = screen.getByRole("list", { name: "Core version" })
    expect(within(facts).getByRole("listitem", { name: "kinby" }).textContent).toBe("kinbyaaaaaaa")
    expect(screen.getByText("Behind the hub")).toBeDefined()
    expect(
      screen.getByText("The instance runs kinby aaaaaaa, and the hub is at bbbbbbb."),
    ).toBeDefined()
  })

  it("shows no notice for an instance on the hub's revision", () => {
    openSection(instanceSummary({ instance_id: "hub-ada", source_revision: "c".repeat(40) }))

    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("updates core to the hub's revision and shows the operation's steps", async () => {
    let finishedYet = false
    const { caller, clock, onChanged, user } = openSection(behind, {
      "operation.get": () =>
        finishedYet
          ? operation("succeeded", [step("image", "succeeded"), step("ready", "succeeded")])
          : operation("running", [step("image", "running", "Preparing the image.")]),
    })

    await user.click(screen.getByRole("button", { name: "Update core" }))

    const steps = await screen.findByRole("list", { name: "Update steps" })
    expect(within(steps).getByRole("listitem", { name: "image" }).textContent).toContain(
      "Preparing the image.",
    )
    finishedYet = true
    await act(() => clock.advance(1_000))
    expect(await screen.findByText("Updated. The instance runs the new core.")).toBeDefined()
    expect(updates(caller)).toEqual([{ instance_id: "hub-ada", revision: HUB }])
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("updates core to a ref the user types", async () => {
    const { caller, user } = openSection(behind, {
      "operation.get": () => operation("succeeded", [step("ready", "succeeded")]),
    })

    await user.click(screen.getByRole("button", { name: "Another ref" }))
    await user.type(screen.getByRole("textbox", { name: "Ref" }), "v0.3.0")
    await user.click(screen.getByRole("button", { name: "Update core" }))

    await screen.findByText("Updated. The instance runs the new core.")
    expect(updates(caller)).toEqual([{ instance_id: "hub-ada", revision: "v0.3.0" }])
  })

  it("lists what the preflight found, and says the instance keeps running", async () => {
    const found =
      "The command '/bin/sh -c uv sync --frozen' returned a non-zero code: 1\n" +
      "error: The lockfile at `uv.lock` needs to be updated"
    const { onChanged, user } = openSection(behind, {
      "operation.get": () =>
        operation("failed", [step("validate", "succeeded"), step("image", "failed", found)], found),
    })

    await user.click(screen.getByRole("button", { name: "Update core" }))

    const alert = await screen.findByRole("alert", { name: "The update stopped before it began" })
    expect(alert.textContent).toContain("The instance keeps running on its current core.")
    expect(
      within(within(alert).getByRole("list"))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "The command '/bin/sh -c uv sync --frozen' returned a non-zero code: 1",
      "error: The lockfile at `uv.lock` needs to be updated",
    ])
    expect(onChanged).not.toHaveBeenCalled()
  })
})
