import { CallError } from "@kinby/contract"
import type { InstanceSummary, OperationGetResult } from "@kinby/contract"
import {
  type Answers,
  fakeClock,
  instanceSummary,
  type StubCaller,
  stubCaller,
} from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { InstanceHeader } from "@/components/instance-header"

const running = instanceSummary({
  instance_id: "instance-1",
  persona_name: "Ada",
  intended_state: "running",
  process: "running",
  source_revision: "abc1234def5678",
})
const stopped = { ...running, intended_state: "stopped", process: "stopped" } as const

function operation(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-1",
    instance_id: "instance-1",
    kind: "start",
    state: "running",
    detail: "",
    steps: [],
    ...fields,
  }
}

/** Answers each read with the next poll, then keeps answering with the last. */
const inTurn = (polls: OperationGetResult[]) => () =>
  (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult

async function openHeader(answers: Answers, instance: InstanceSummary = running) {
  const caller = stubCaller(answers)
  const clock = fakeClock()
  const onChanged = vi.fn()
  const lent = vi.fn()
  const header = (shown: InstanceSummary) => (
    <InstanceHeader caller={caller} clock={clock} instance={shown} onChanged={onChanged}>
      {(lifecycle) => {
        lent(lifecycle)
        return (
          <button type="button" disabled={lifecycle.starting} onClick={lifecycle.start}>
            Start from the tab
          </button>
        )
      }}
    </InstanceHeader>
  )
  const { rerender } = render(header(instance))
  await act(() => clock.advance(0))
  // What the header shows once the instances are listed again with `listed` in them.
  const relist = (listed: InstanceSummary) => rerender(header(listed))
  return { caller, clock, onChanged, relist, lent, user: userEvent.setup() }
}

describe("the instance header", () => {
  it("names the instance, with no Home badge on another than the home instance", async () => {
    await openHeader({})

    expect(screen.getByRole("heading", { level: 1, name: "Ada" })).toBeDefined()
    expect(screen.queryByText("Home")).toBeNull()
  })

  it("marks the home instance", async () => {
    await openHeader({}, { ...running, home: true })

    expect(screen.getByText("Home")).toBeDefined()
  })

  it.each([
    ["running", running, "Running · core abc1234"],
    ["stopped", stopped, "Stopped · core abc1234"],
    ["stopped after a crash", { ...stopped, process: "failed" }, "Stopped · core abc1234"],
    [
      "restarting",
      { ...running, process: "starting", detail: "restarting" },
      "Restarting · core abc1234",
    ],
    ["failed", { ...running, process: "failed", detail: "exited (1)" }, "Failed · core abc1234"],
    ["without a revision", { ...running, source_revision: "" }, "Running"],
  ] as const)("says what the runtime saw when it is %s", async (_state, instance, line) => {
    await openHeader({}, instance)

    expect(screen.getByText(line)).toBeDefined()
  })
})

describe("starting from the header", () => {
  it("starts it, busy until it runs, and lists the instances again once it does", async () => {
    const { user, clock, caller, onChanged, relist } = await openHeader(
      {
        "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
        "operation.get": inTurn([
          operation({ state: "running" }),
          operation({ state: "succeeded" }),
        ]),
      },
      stopped,
    )

    await user.click(screen.getByRole("button", { name: "Start" }))
    await act(() => clock.advance(0))
    const start = screen.getByRole("button", { name: /Start$/ })
    expect(start.hasAttribute("disabled")).toBe(true)
    expect(within(start).getByRole("status", { name: "Loading" })).toBeDefined()
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(caller.calls.find((call) => call.method === "instance.start")?.params).toEqual({
      instance_id: "instance-1",
    })
    expect(onChanged).toHaveBeenCalledOnce()
    relist(running)
    expect(screen.getByRole("button", { name: "Stop" })).toBeDefined()
    expect(screen.queryByRole("button", { name: /Start$/ })).toBeNull()
  })

  it("says why a start failed under the header", async () => {
    const { user, clock, onChanged } = await openHeader(
      {
        "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
        "operation.get": () => operation({ state: "failed", detail: "The image is gone." }),
      },
      stopped,
    )

    await user.click(screen.getByRole("button", { name: "Start" }))
    await act(() => clock.advance(0))

    const alert = within(screen.getByRole("alert"))
    expect(alert.getByText("The start failed")).toBeDefined()
    expect(alert.getByText("The image is gone.")).toBeDefined()
    expect(screen.getByRole("button", { name: "Start" }).hasAttribute("disabled")).toBe(false)
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("lends its start to the page under it, busy there too", async () => {
    const { user, clock, caller, lent } = await openHeader(
      {
        "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
        "operation.get": () => operation({ state: "running" }),
      },
      stopped,
    )

    await user.click(screen.getByRole("button", { name: "Start from the tab" }))
    await act(() => clock.advance(0))

    expect(caller.calls.filter((call) => call.method === "instance.start")).toHaveLength(1)
    expect(screen.getByRole("button", { name: /Start$/ }).hasAttribute("disabled")).toBe(true)
    expect(
      screen.getByRole("button", { name: "Start from the tab" }).hasAttribute("disabled"),
    ).toBe(true)
    expect(lent).toHaveBeenLastCalledWith(expect.objectContaining({ stopping: false }))
  })

  it("offers Start again once it ran and stopped", async () => {
    const { user, clock, relist } = await openHeader(
      {
        "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
        "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
        "operation.get": (params) =>
          operation({
            operation_id: (params as { operation_id: string }).operation_id,
            state: "succeeded",
          }),
      },
      stopped,
    )

    await user.click(screen.getByRole("button", { name: "Start" }))
    await act(() => clock.advance(0))
    relist(running)
    await confirmStop(user)
    await act(() => clock.advance(0))
    relist(stopped)

    expect(screen.getByRole("button", { name: "Start" }).hasAttribute("disabled")).toBe(false)
  })
})

describe("stopping from the header", () => {
  it.each([
    ["running", running],
    ["restarting", { ...running, process: "starting", detail: "restarting" }],
    ["failed", { ...running, process: "failed", detail: "exited (1)" }],
  ] as const)("offers Stop when its process is %s", async (_process, instance) => {
    await openHeader({}, instance)

    expect(screen.getByRole("button", { name: "Stop" })).toBeDefined()
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull()
  })

  it("asks first, and makes no call when the stop is cancelled", async () => {
    const { user, caller } = await openHeader({})

    await user.click(screen.getByRole("button", { name: "Stop" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Stop Ada?" })
    expect(dialog.textContent).toContain(
      "Ada finishes the work it has accepted, then does nothing until it starts again.",
    )
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    expect(caller.calls.some((call) => call.method === "instance.stop")).toBe(false)
  })

  it("stops it once confirmed, busy until the stop ends", async () => {
    const { user, clock, caller, onChanged } = await openHeader({
      "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
      "operation.get": () => operation({ kind: "stop", state: "running" }),
    })

    await confirmStop(user)
    await act(() => clock.advance(0))

    expect(stops(caller)).toEqual([{ instance_id: "instance-1" }])
    const stop = screen.getByRole("button", { name: /Stop$/ })
    expect(stop.hasAttribute("disabled")).toBe(true)
    expect(within(stop).getByRole("status", { name: "Loading" })).toBeDefined()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("lists the instances again once it stops, and offers Start", async () => {
    const { user, clock, onChanged, relist } = await openHeader({
      "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
      "operation.get": inTurn([
        operation({ kind: "stop", state: "running" }),
        operation({ kind: "stop", state: "succeeded" }),
      ]),
    })

    await confirmStop(user)
    await act(() => clock.advance(0))
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(onChanged).toHaveBeenCalledOnce()
    relist(stopped)
    expect(screen.getByRole("button", { name: "Start" })).toBeDefined()
    expect(screen.queryByRole("button", { name: /Stop$/ })).toBeNull()
  })

  it("says why a stop failed under the header, and offers to force it", async () => {
    const unreachable = "Its lifecycle endpoint did not answer. Force stop it."
    const { user, clock, caller, onChanged } = await openHeader({
      "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
      "operation.get": inTurn([
        operation({ kind: "stop", state: "failed", detail: unreachable }),
        operation({ kind: "stop", state: "succeeded" }),
      ]),
    })

    expect(screen.queryByRole("button", { name: "Force stop" })).toBeNull()
    await confirmStop(user)
    await act(() => clock.advance(0))
    const alert = within(screen.getByRole("alert"))
    expect(alert.getByText("The stop failed")).toBeDefined()
    expect(alert.getByText(unreachable)).toBeDefined()
    expect(onChanged).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(0))

    expect(stops(caller)).toEqual([
      { instance_id: "instance-1" },
      { instance_id: "instance-1", force: true },
    ])
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("keeps Stop and Force stop when a relist marks it stopped mid-drain, and says so below", async () => {
    const { user, clock, relist, lent } = await openHeader({
      "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
      "operation.get": () => operation({ kind: "stop", state: "running" }),
    })

    await confirmStop(user)
    await act(() => clock.advance(0))
    relist({ ...running, intended_state: "stopped" })
    await act(() => clock.advance(0))

    expect(screen.queryByRole("button", { name: "Start" })).toBeNull()
    expect(screen.getByRole("button", { name: "Force stop" })).toBeDefined()
    expect(lent).toHaveBeenLastCalledWith(expect.objectContaining({ stopping: true }))
  })

  it("forces a stop that is still draining, and keeps following it", async () => {
    const { user, clock, caller, onChanged } = await openHeader({
      "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
      "operation.get": inTurn([
        operation({ operation_id: "op-stop", kind: "stop", state: "running" }),
        operation({ operation_id: "op-stop", kind: "stop", state: "running" }),
        operation({ operation_id: "op-stop", kind: "stop", state: "succeeded" }),
      ]),
    })

    await confirmStop(user)
    await act(() => clock.advance(0))
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(0))
    expect(screen.getByRole("button", { name: "Force stop" }).hasAttribute("disabled")).toBe(true)
    expect(screen.getByRole("button", { name: /Stop$/ }).hasAttribute("disabled")).toBe(true)
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(stops(caller)).toEqual([
      { instance_id: "instance-1" },
      { instance_id: "instance-1", force: true },
    ])
    expect(new Set(followed(caller))).toEqual(new Set(["op-stop"]))
    expect(onChanged).toHaveBeenCalledOnce()
  })
})

describe("removing from the header", () => {
  it.each([
    ["running", running],
    ["stopped", stopped],
  ] as const)("offers Remove in the ⋯ menu when it is %s", async (_state, instance) => {
    const { user } = await openHeader({}, instance)

    await user.click(screen.getByRole("button", { name: "More actions" }))

    expect(await screen.findByRole("menuitem", { name: "Remove" })).toBeDefined()
  })

  it.each([
    ["running", running],
    ["stopped", stopped],
  ] as const)("offers no Remove for the home instance when it is %s", async (_state, instance) => {
    const { user } = await openHeader({}, { ...instance, home: true })

    await user.click(screen.getByRole("button", { name: "More actions" }))

    expect(await screen.findByRole("menuitem", { name: "Update core" })).toBeDefined()
    expect(screen.queryByRole("menuitem", { name: "Remove" })).toBeNull()
  })

  it("asks first, and makes no call when the removal is cancelled", async () => {
    const { user, caller } = await openHeader({})

    const dialog = await askToRemove(user)
    expect(dialog.textContent).toContain(
      "Ada stops and leaves the sidebar. Its data stays, and you can restore it from Removed instances.",
    )
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())

    expect(removals(caller)).toEqual([])
  })

  it("removes it once confirmed", async () => {
    const { user, clock, caller } = await openHeader(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "remove", state: "running" }),
      },
      stopped,
    )

    await confirmRemoval(user)
    await act(() => clock.advance(0))

    expect(removals(caller)).toEqual([{ instance_id: "instance-1" }])
  })

  it("lists the instances again once it is removed, and goes home", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const { user, clock, onChanged } = await openHeader({
      "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
      "operation.get": inTurn([
        operation({ kind: "remove", state: "running" }),
        operation({ kind: "remove", state: "succeeded" }),
      ]),
    })

    await confirmRemoval(user)
    await act(() => clock.advance(0))
    expect(onChanged).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/instances/instance-1")
    await act(() => clock.advance(1_000))

    expect(onChanged).toHaveBeenCalledOnce()
    expect(window.location.pathname).toBe("/")
  })

  it("says it is removing while the removal drains, and keeps following it once forced", async () => {
    const { user, clock, caller, onChanged } = await openHeader({
      "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
      // A force stop escalates the pending removal, and the hub answers with its operation.
      "instance.stop": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
      "operation.get": inTurn([
        operation({ operation_id: "op-remove", kind: "remove", state: "running" }),
        operation({ operation_id: "op-remove", kind: "remove", state: "succeeded" }),
      ]),
    })

    expect(screen.queryByText("Removing…")).toBeNull()
    await confirmRemoval(user)
    await act(() => clock.advance(0))
    expect(screen.getByText("Removing…")).toBeDefined()
    expect(screen.getByRole("button", { name: "More actions" }).hasAttribute("disabled")).toBe(true)
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(0))
    expect(screen.getByRole("button", { name: "Force stop" }).hasAttribute("disabled")).toBe(true)
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(stops(caller)).toEqual([{ instance_id: "instance-1", force: true }])
    expect(new Set(followed(caller))).toEqual(new Set(["op-remove"]))
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("goes home when the removal ends before its Force stop lands", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const { user, clock, onChanged } = await openHeader({
      "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
      "instance.stop": () => {
        throw new CallError({ code: "NOT_FOUND", message: "no such instance", retryable: false })
      },
      "operation.get": inTurn([
        operation({ operation_id: "op-remove", kind: "remove", state: "running" }),
        operation({ operation_id: "op-remove", kind: "remove", state: "succeeded" }),
      ]),
    })

    await confirmRemoval(user)
    await act(() => clock.advance(0))
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(1_000))

    expect(screen.queryByText("The removal failed")).toBeNull()
    expect(onChanged).toHaveBeenCalledOnce()
    expect(window.location.pathname).toBe("/")
  })

  it("reports a removal that failed before its Force stop landed, whatever the stop does", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const detail = "The container could not be removed: permission denied."
    const polls = inTurn([
      operation({ operation_id: "op-remove", kind: "remove", state: "running" }),
      operation({ operation_id: "op-remove", kind: "remove", state: "failed", detail }),
    ])
    const { user, clock, caller, onChanged } = await openHeader({
      "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
      // With the removal over, the hub starts a stop of its own.
      "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
      "operation.get": (params) =>
        (params as { operation_id: string }).operation_id === "op-stop"
          ? operation({ operation_id: "op-stop", kind: "stop", state: "succeeded" })
          : polls(),
    })

    await confirmRemoval(user)
    await act(() => clock.advance(0))
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(1_000))

    expect(within(screen.getByRole("alert")).getByText(detail)).toBeDefined()
    expect(new Set(followed(caller))).toEqual(new Set(["op-remove"]))
    expect(onChanged).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/instances/instance-1")
  })

  it("shows the hub's message in the dialog when the hub refuses the removal", async () => {
    const refusal =
      "The Codex sign-in is still running for this instance. Finish the sign-in or let its code expire first."
    const { user, clock, onChanged } = await openHeader({
      "instance.remove": () => {
        throw new CallError({ code: "INSTANCE_BUSY", message: refusal, retryable: false })
      },
    })

    const dialog = await askToRemove(user)
    await user.click(within(dialog).getByRole("button", { name: "Remove" }))
    await act(() => clock.advance(0))

    expect(within(screen.getByRole("alertdialog")).getByText(refusal)).toBeDefined()
    expect(screen.queryByText("Removing…")).toBeNull()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("says why a removal failed under the header, and offers Remove again", async () => {
    const detail = "The container could not be removed: permission denied."
    const { user, clock, onChanged } = await openHeader(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "remove", state: "failed", detail }),
      },
      stopped,
    )

    await confirmRemoval(user)
    await act(() => clock.advance(0))

    const alert = within(screen.getByRole("alert"))
    expect(alert.getByText("The removal failed")).toBeDefined()
    expect(alert.getByText(detail)).toBeDefined()
    expect(screen.queryByText("Removing…")).toBeNull()
    expect(screen.queryByRole("button", { name: "Force stop" })).toBeNull()
    expect(screen.getByRole("button", { name: "More actions" }).hasAttribute("disabled")).toBe(
      false,
    )
    expect(onChanged).not.toHaveBeenCalled()
  })
})

describe("the header's ⋯ menu", () => {
  it("opens Config on Core version from Update core", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const { user } = await openHeader({})

    await user.click(screen.getByRole("button", { name: "More actions" }))
    await user.click(await screen.findByRole("menuitem", { name: "Update core" }))

    expect(window.location.pathname).toBe("/instances/instance-1/config/core")
  })
})

describe("the process alert", () => {
  it("says it is restarting when its container restarts in a loop, and opens Config", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/routines")
    const { user } = await openHeader({}, { ...running, process: "starting", detail: "restarting" })

    const alert = within(screen.getByRole("alert"))
    expect(alert.getByText("Ada is restarting")).toBeDefined()
    expect(
      alert.getByText("Its container keeps stopping, and the runtime keeps starting it again."),
    ).toBeDefined()
    await user.click(alert.getByRole("button", { name: "Open Config" }))

    expect(window.location.pathname).toBe("/instances/instance-1/config")
  })

  it("says it failed, with what the runtime saw", async () => {
    await openHeader({}, { ...running, process: "failed", detail: "exited (1)" })

    const alert = within(screen.getByRole("alert"))
    expect(alert.getByText("Ada failed")).toBeDefined()
    expect(alert.getByText("exited (1)")).toBeDefined()
    expect(alert.getByText(/^Check its settings in Configure before you stop it/)).toBeDefined()
  })

  it.each([
    ["running", running],
    ["stopped after a crash", { ...stopped, process: "failed", detail: "exited (1)" }],
  ] as const)("says nothing when it is %s", async (_state, instance) => {
    await openHeader({}, instance)

    expect(screen.queryByRole("alert")).toBeNull()
  })
})

async function askToRemove(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "More actions" }))
  await user.click(await screen.findByRole("menuitem", { name: "Remove" }))
  return screen.findByRole("alertdialog", { name: "Remove Ada?" })
}

async function confirmRemoval(user: ReturnType<typeof userEvent.setup>) {
  const dialog = await askToRemove(user)
  await user.click(within(dialog).getByRole("button", { name: "Remove" }))
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
}

async function confirmStop(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Stop" }))
  const dialog = await screen.findByRole("alertdialog", { name: "Stop Ada?" })
  await user.click(within(dialog).getByRole("button", { name: "Stop" }))
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
}

const removals = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.remove").map((call) => call.params)

const stops = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.stop").map((call) => call.params)

/** The operations the header read. */
const followed = (caller: StubCaller) =>
  caller.calls
    .filter((call) => call.method === "operation.get")
    .map((call) => (call.params as { operation_id: string }).operation_id)
