import { CallError } from "@kinby/contract"
import type {
  Client,
  InstanceSetup,
  InstanceStatusResult,
  InstanceSummary,
  OperationGetResult,
} from "@kinby/contract"
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

import { InstancePage } from "@/components/instance-page"

const setup: InstanceSetup = {
  logins: [
    {
      id: "codex",
      label: "Codex",
      description: "Signs Codex in with your ChatGPT plan.",
      state: "pending",
    },
    {
      id: "editor",
      label: "Editor account",
      description: "Signs the editor in with your subscription.",
      state: "signed_in",
    },
  ],
  secrets: [
    {
      name: "api_key",
      variable: "ANTHROPIC_API_KEY",
      label: "API key",
      required: true,
      is_set: true,
    },
    {
      name: "GH_TOKEN",
      variable: "GH_TOKEN",
      label: "GitHub token",
      required: true,
      is_set: false,
    },
    {
      name: "WEBHOOK_SECRET",
      variable: "WEBHOOK_SECRET",
      label: "Webhook secret",
      required: false,
      is_set: false,
    },
  ],
}

const stopped = instanceSummary({
  instance_id: "instance-1",
  persona_name: "Ada",
  intended_state: "stopped",
  setup_pending: true,
})

function status(fields: Partial<InstanceStatusResult> = {}): InstanceStatusResult {
  return {
    instance_id: "instance-1",
    process: "created",
    readiness: "not-running",
    setup,
    recreate_reasons: [],
    ...fields,
  }
}

function operation(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-1",
    instance_id: "instance-1",
    kind: "login",
    state: "running",
    detail: "",
    steps: [],
    ...fields,
  }
}

const waitingForCode = operation({
  steps: [
    {
      name: "sign-in",
      state: "running",
      detail: "Waiting for you to sign in to Codex.",
      prompt: { url: "https://auth.openai.com/codex/device", code: "ABCD-12345" },
    },
  ],
})

async function openPage(answers: Answers, instance = stopped) {
  const caller = stubCaller({ "instance.status": () => status(), ...answers })
  const clock = fakeClock()
  const onChanged = vi.fn()
  const page = (shown: InstanceSummary) => (
    <InstancePage caller={caller} clock={clock} instance={shown} onChanged={onChanged} />
  )
  const { rerender } = render(page(instance))
  await act(() => clock.advance(0))
  // What the page shows once the instances are listed again with `listed` in them.
  const relist = (listed: InstanceSummary) => rerender(page(listed))
  return { caller, clock, onChanged, relist, user: userEvent.setup() }
}

const card = () => within(screen.getByRole("region", { name: "Finish setting up Ada" }))
const login = (name: string) =>
  within(
    within(screen.getByRole("list", { name: "Subscription logins" })).getByRole("listitem", {
      name,
    }),
  )
const secret = (name: string) =>
  within(within(screen.getByRole("list", { name: "Secrets" })).getByRole("listitem", { name }))

describe("the setup card", () => {
  it("opens a stopped instance with pending setup on each login and secret, and Start", async () => {
    await openPage({})

    expect(card().getByRole("button", { name: "Start" })).toBeDefined()
    expect(login("Codex").getByText("Not signed in")).toBeDefined()
    expect(login("Codex").getByRole("button", { name: "Sign in" })).toBeDefined()
    expect(login("Editor account").getByText("Signed in").getAttribute("data-variant")).toBe(
      "success",
    )
    expect(login("Editor account").getByRole("button", { name: "Sign in again" })).toBeDefined()
    expect(secret("API key").getByText("Set")).toBeDefined()
    expect(secret("GitHub token").getByText("Not set")).toBeDefined()
    expect(secret("Webhook secret").getByText("Optional")).toBeDefined()
    expect(secret("Webhook secret").getByText("Not set")).toBeDefined()
  })

  it("reads a login whose last sign-in failed as failed", async () => {
    const failed: InstanceSetup = {
      ...setup,
      logins: [{ ...setup.logins[0]!, state: "failed" }],
    }
    await openPage({ "instance.status": () => status({ setup: failed }) })

    expect(login("Codex").getByText("Failed")).toBeDefined()
    expect(login("Codex").getByRole("button", { name: "Sign in again" })).toBeDefined()
  })

  it("finishes a sign-in there, and lists the instances again once it ends", async () => {
    const polls = [waitingForCode, operation({ state: "succeeded", detail: "Signed in." })]
    const { user, clock, caller, onChanged } = await openPage({
      "instance.login.start": () => ({ operation_id: "op-1", instance_id: "instance-1" }),
      "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
    })

    await user.click(login("Codex").getByRole("button", { name: "Sign in" }))
    await act(() => clock.advance(0))
    expect(login("Codex").getByText("ABCD-12345")).toBeDefined()
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(caller.calls.find((call) => call.method === "instance.login.start")?.params).toEqual({
      instance_id: "instance-1",
      login_id: "codex",
    })
    expect(login("Codex").getByText("Signed in")).toBeDefined()
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("follows a sign-in that runs when the page loads, and lists the instances again once it ends", async () => {
    const signingIn: InstanceSetup = {
      ...setup,
      logins: [{ ...setup.logins[0]!, operation_id: "op-1" }, setup.logins[1]!],
    }
    const polls = [waitingForCode, operation({ state: "succeeded", detail: "Signed in." })]
    const { clock, caller, onChanged } = await openPage({
      "instance.status": () => status({ setup: signingIn }),
      "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
    })

    expect(login("Codex").getByText("ABCD-12345")).toBeDefined()
    expect(login("Codex").getByText("Waiting")).toBeDefined()
    expect(login("Codex").getByRole("button", { name: "Sign in" })).toHaveProperty("disabled", true)
    expect(login("Editor account").getByText("Signed in")).toBeDefined()
    await act(() => clock.advance(1_000))

    expect(caller.calls.some((call) => call.method === "instance.login.start")).toBe(false)
    expect(login("Codex").getByText("Signed in")).toBeDefined()
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("keeps a signed-in login signed in when signing it in again expires, and says so", async () => {
    const detail = "The code expired. Sign in again for a new one."
    const { user, clock, onChanged } = await openPage({
      "instance.login.start": () => ({ operation_id: "op-1", instance_id: "instance-1" }),
      "operation.get": () => operation({ state: "failed", detail }),
    })

    await user.click(login("Editor account").getByRole("button", { name: "Sign in again" }))
    await act(() => clock.advance(0))

    expect(onChanged).toHaveBeenCalledOnce()
    expect(login("Editor account").getByText("Signed in").getAttribute("data-variant")).toBe(
      "success",
    )
    expect(login("Editor account").queryByText("Failed")).toBeNull()
    expect(
      login("Editor account").getByText(
        `The new sign-in did not finish, and the one before it still works. ${detail}`,
      ),
    ).toBeDefined()
    expect(login("Editor account").getByRole("button", { name: "Sign in again" })).toBeDefined()
  })

  it("says a sign-in failed on a login that was not signed in", async () => {
    const detail = "The code expired. Sign in again for a new one."
    const { user, clock } = await openPage({
      "instance.login.start": () => ({ operation_id: "op-1", instance_id: "instance-1" }),
      "operation.get": () => operation({ state: "failed", detail }),
    })

    await user.click(login("Codex").getByRole("button", { name: "Sign in" }))
    await act(() => clock.advance(0))

    expect(login("Codex").getByText("Failed")).toBeDefined()
    expect(login("Codex").getByText(detail)).toBeDefined()
    expect(login("Codex").getByRole("button", { name: "Sign in again" })).toBeDefined()
  })

  it("sets a secret and replaces one there, and lists the instances again once the hub holds it", async () => {
    const { user, clock, caller, onChanged } = await openPage({
      "instance.secrets.set": () => ({ operation_id: "op-secret", instance_id: "instance-1" }),
      "operation.get": () => operation({ kind: "secrets", state: "succeeded" }),
    })

    expect(secret("API key").queryByRole("button", { name: "Set" })).toBeNull()
    await user.click(secret("API key").getByRole("button", { name: "Replace" }))
    await user.type(secret("API key").getByLabelText("New value for API key"), "sk-new")
    await user.click(secret("API key").getByRole("button", { name: "Save" }))
    await act(() => clock.advance(0))
    await user.click(secret("GitHub token").getByRole("button", { name: "Set" }))
    await user.type(secret("GitHub token").getByLabelText("New value for GitHub token"), "ghp-1")
    await user.click(secret("GitHub token").getByRole("button", { name: "Save" }))
    await act(() => clock.advance(0))

    expect(
      caller.calls
        .filter((call) => call.method === "instance.secrets.set")
        .map((call) => call.params),
    ).toEqual([
      { instance_id: "instance-1", secrets: { api_key: "sk-new" } },
      { instance_id: "instance-1", secrets: { GH_TOKEN: "ghp-1" } },
    ])
    expect(onChanged).toHaveBeenCalledTimes(2)
    expect(secret("GitHub token").queryByLabelText("New value for GitHub token")).toBeNull()
    expect(card().getByText(/A secret you set applies when Ada starts/)).toBeDefined()
  })

  it("says why a secret was not set, and keeps what was typed", async () => {
    const { user, clock, onChanged } = await openPage({
      "instance.secrets.set": () => ({ operation_id: "op-secret", instance_id: "instance-1" }),
      "operation.get": () =>
        operation({ kind: "secrets", state: "failed", detail: "No space left on device." }),
    })

    await user.click(secret("GitHub token").getByRole("button", { name: "Set" }))
    await user.type(secret("GitHub token").getByLabelText("New value for GitHub token"), "ghp-1")
    await user.click(secret("GitHub token").getByRole("button", { name: "Save" }))
    await act(() => clock.advance(0))

    expect(secret("GitHub token").getByText("No space left on device.")).toBeDefined()
    expect(secret("GitHub token").getByLabelText("New value for GitHub token")).toHaveProperty(
      "value",
      "ghp-1",
    )
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("starts the instance anyway, and lists the instances again once it runs", async () => {
    const { user, clock, caller, onChanged } = await openPage({
      "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
      "operation.get": () => operation({ kind: "start", state: "succeeded" }),
    })

    await user.click(card().getByRole("button", { name: "Start" }))
    await act(() => clock.advance(0))

    expect(caller.calls.find((call) => call.method === "instance.start")?.params).toEqual({
      instance_id: "instance-1",
    })
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("says why a start failed", async () => {
    const { user, clock } = await openPage({
      "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
      "operation.get": () =>
        operation({ kind: "start", state: "failed", detail: "The image is gone." }),
    })

    await user.click(card().getByRole("button", { name: "Start" }))
    await act(() => clock.advance(0))

    expect(card().getByText("The image is gone.")).toBeDefined()
    expect(card().getByRole("button", { name: "Start" }).hasAttribute("disabled")).toBe(false)
  })
})

describe("a stopped instance with setup complete", () => {
  const complete = { ...stopped, setup_pending: false }
  const stoppedView = () => within(screen.getByRole("region", { name: "Ada is stopped" }))

  it("offers Start instead of the setup card", async () => {
    await openPage({}, complete)

    expect(stoppedView().getByRole("button", { name: "Start" })).toBeDefined()
    expect(screen.queryByRole("region", { name: "Finish setting up Ada" })).toBeNull()
  })

  it("says the last run ended with an error when it crashed before the stop", async () => {
    await openPage({}, { ...complete, process: "failed", detail: "exited (1)" })

    expect(stoppedView().getByText("The last run ended with an error: exited (1)")).toBeDefined()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("says nothing of the last run when it exited cleanly", async () => {
    await openPage({}, { ...complete, process: "stopped", detail: "exited (0)" })

    expect(stoppedView().queryByText(/The last run ended with an error/)).toBeNull()
  })

  it("starts it, and leaves the stopped view once it runs", async () => {
    const polls = [
      operation({ kind: "start", state: "running" }),
      operation({ kind: "start", state: "succeeded" }),
    ]
    const { user, clock, caller, onChanged, relist } = await openPage(
      {
        "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      complete,
    )

    await user.click(stoppedView().getByRole("button", { name: "Start" }))
    await act(() => clock.advance(0))
    expect(stoppedView().getByRole("status", { name: "Loading" })).toBeDefined()
    expect(stoppedView().getByRole("button", { name: /Start/ }).hasAttribute("disabled")).toBe(true)
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(caller.calls.find((call) => call.method === "instance.start")?.params).toEqual({
      instance_id: "instance-1",
    })
    expect(onChanged).toHaveBeenCalledOnce()
    relist({ ...complete, intended_state: "running" })
    await act(() => clock.advance(0))
    expect(screen.queryByRole("region", { name: "Ada is stopped" })).toBeNull()
  })

  it("says why a start failed", async () => {
    const { user, clock, onChanged } = await openPage(
      {
        "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
        "operation.get": () =>
          operation({ kind: "start", state: "failed", detail: "The image is gone." }),
      },
      complete,
    )

    await user.click(stoppedView().getByRole("button", { name: "Start" }))
    await act(() => clock.advance(0))

    expect(stoppedView().getByText("The image is gone.")).toBeDefined()
    expect(stoppedView().getByRole("button", { name: "Start" }).hasAttribute("disabled")).toBe(
      false,
    )
    expect(onChanged).not.toHaveBeenCalled()
  })
})

describe("a running instance", () => {
  const running = { ...stopped, intended_state: "running" } as const

  it("offers signing in again, without the setup card", async () => {
    await openPage({ "instance.status": () => status({ process: "running" }) }, running)

    expect(screen.queryByRole("region", { name: "Finish setting up Ada" })).toBeNull()
    expect(login("Editor account").getByRole("button", { name: "Sign in again" })).toBeDefined()
    expect(login("Codex").getByRole("button", { name: "Sign in" })).toBeDefined()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("says it is restarting when its container restarts in a loop", async () => {
    await openPage({}, { ...running, process: "starting", detail: "restarting" })

    expect(within(screen.getByRole("alert")).getByText("Ada is restarting")).toBeDefined()
    expect(login("Codex").getByRole("button", { name: "Sign in" })).toBeDefined()
  })

  it("says it failed, with what the runtime saw", async () => {
    const failed = { ...running, process: "failed", detail: "exited (1)" } as const
    await openPage({ "instance.status": () => status({ setup: { ...setup, logins: [] } }) }, failed)

    const alert = within(screen.getByRole("alert"))
    expect(alert.getByText("Ada failed")).toBeDefined()
    expect(alert.getByText("exited (1)")).toBeDefined()
  })

  it("follows a sign-in that runs when the page loads", async () => {
    const signingIn: InstanceSetup = {
      ...setup,
      logins: [setup.logins[0]!, { ...setup.logins[1]!, operation_id: "op-1" }],
    }
    await openPage(
      {
        "instance.status": () => status({ process: "running", setup: signingIn }),
        "operation.get": () => waitingForCode,
      },
      running,
    )

    expect(login("Editor account").getByText("ABCD-12345")).toBeDefined()
    expect(login("Editor account").getByText("Waiting")).toBeDefined()
    expect(login("Codex").getByText("Not signed in")).toBeDefined()
  })

  it("asks to sign in to a login that is not signed in yet", async () => {
    await openPage({ "instance.status": () => status({ process: "running" }) }, running)

    expect(
      screen.getByText(
        "Codex is not signed in yet. Sign in to let the instance use it. The instance keeps running.",
      ),
    ).toBeDefined()
    expect(screen.queryByText(/Sign in again when/)).toBeNull()
  })

  it("names every login not signed in, a failed one too", async () => {
    const logins: InstanceSetup["logins"] = [
      { ...setup.logins[0]!, state: "pending" },
      { ...setup.logins[1]!, state: "failed" },
      { id: "claude", label: "Claude", description: "Signs Claude in.", state: "signed_in" },
    ]
    await openPage({ "instance.status": () => status({ setup: { ...setup, logins } }) }, running)

    expect(
      screen.getByText(
        "Codex and Editor account are not signed in yet. Sign in to let the instance use them. The instance keeps running.",
      ),
    ).toBeDefined()
  })

  it("offers signing in again once every login is signed in", async () => {
    const logins = setup.logins.map((login) => ({ ...login, state: "signed_in" as const }))
    await openPage({ "instance.status": () => status({ setup: { ...setup, logins } }) }, running)

    expect(
      screen.getByText(
        "Sign in again when a subscription stops working. The instance keeps running.",
      ),
    ).toBeDefined()
  })

  // jsdom lays nothing out, so the layout shows only in the classes.
  it("gives a login's description the row's width on a phone, above its badge and button", async () => {
    await openPage({ "instance.status": () => status({ process: "running" }) }, running)

    const description = login("Codex").getByText("Signs Codex in with your ChatGPT plan.")
    expect(description.className).not.toMatch(/line-clamp-\d/)
    const text = description.parentElement?.classList
    expect(text?.contains("basis-full") && text.contains("sm:basis-0")).toBe(true)
  })

  it("shows no sign-in rows when it declares no login", async () => {
    await openPage(
      { "instance.status": () => status({ setup: { ...setup, logins: [] } }) },
      running,
    )

    expect(screen.queryByRole("list", { name: "Subscription logins" })).toBeNull()
    expect(screen.getByText("Nothing here yet")).toBeDefined()
  })

  it("says there is nothing here when the status cannot be read", async () => {
    const caller: Pick<Client, "call"> = {
      call: () => Promise.reject(new Error("gone")),
    }
    const clock = fakeClock()
    render(<InstancePage caller={caller} clock={clock} instance={running} onChanged={vi.fn()} />)
    await act(() => clock.advance(0))

    expect(screen.getByText("Nothing here yet")).toBeDefined()
    expect(screen.queryByRole("list", { name: "Subscription logins" })).toBeNull()
  })

  it("does not say there is nothing here before the status is read", () => {
    const caller: Pick<Client, "call"> = {
      call: () => new Promise<never>(() => {}),
    }
    render(
      <InstancePage caller={caller} clock={fakeClock()} instance={running} onChanged={vi.fn()} />,
    )

    expect(screen.queryByText("Nothing here yet")).toBeNull()
    expect(screen.queryByRole("list", { name: "Subscription logins" })).toBeNull()
  })
})

describe("stopping a running instance", () => {
  const running = { ...stopped, intended_state: "running", process: "running" } as const

  it.each([
    ["running", running],
    ["restarting", { ...running, process: "starting", detail: "restarting" }],
    ["failed", { ...running, process: "failed", detail: "exited (1)" }],
  ] as const)("offers Stop when its process is %s", async (_process, instance) => {
    await openPage({}, instance)

    expect(screen.getByRole("button", { name: "Stop" })).toBeDefined()
  })

  it("asks first, and makes no call when the stop is cancelled", async () => {
    const { user, caller } = await openPage({}, running)

    await user.click(screen.getByRole("button", { name: "Stop" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Stop Ada?" })
    expect(dialog.textContent).toContain(
      "Ada finishes the work it has accepted, then does nothing until it starts again.",
    )
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    expect(caller.calls.some((call) => call.method === "instance.stop")).toBe(false)
  })

  it("stops it once confirmed, busy until the stop ends", async () => {
    const { user, clock, caller, onChanged } = await openPage(
      {
        "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "stop", state: "running" }),
      },
      running,
    )

    await confirmStop(user)
    await act(() => clock.advance(0))

    expect(stops(caller)).toEqual([{ instance_id: "instance-1" }])
    const stop = screen.getByRole("button", { name: /Stop/ })
    expect(stop.hasAttribute("disabled")).toBe(true)
    expect(within(stop).getByRole("status", { name: "Loading" })).toBeDefined()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("lists the instances again once it stops, and shows it stopped", async () => {
    const polls = [
      operation({ kind: "stop", state: "running" }),
      operation({ kind: "stop", state: "succeeded" }),
    ]
    const { user, clock, onChanged, relist } = await openPage(
      {
        "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      running,
    )

    await confirmStop(user)
    await act(() => clock.advance(0))
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(onChanged).toHaveBeenCalledOnce()
    relist({ ...running, intended_state: "stopped", setup_pending: false })
    await act(() => clock.advance(0))
    expect(screen.getByRole("region", { name: "Ada is stopped" })).toBeDefined()
  })

  it("says why a stop failed, and offers to force it", async () => {
    const unreachable = "Its lifecycle endpoint did not answer. Force stop it."
    const polls = [
      operation({ kind: "stop", state: "failed", detail: unreachable }),
      operation({ kind: "stop", state: "succeeded" }),
    ]
    const { user, clock, caller, onChanged } = await openPage(
      {
        "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      running,
    )

    expect(screen.queryByRole("button", { name: "Force stop" })).toBeNull()
    await confirmStop(user)
    await act(() => clock.advance(0))
    expect(within(screen.getByRole("alert")).getByText(unreachable)).toBeDefined()
    expect(onChanged).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(0))

    expect(stops(caller)).toEqual([
      { instance_id: "instance-1" },
      { instance_id: "instance-1", force: true },
    ])
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("keeps the stop and Force stop when a relist marks it stopped mid-drain", async () => {
    const { user, clock, relist } = await openPage(
      {
        "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "stop", state: "running" }),
      },
      running,
    )

    await confirmStop(user)
    await act(() => clock.advance(0))
    relist({ ...running, intended_state: "stopped", setup_pending: false })
    await act(() => clock.advance(0))

    expect(screen.queryByRole("region", { name: "Ada is stopped" })).toBeNull()
    expect(screen.getByRole("button", { name: "Force stop" })).toBeDefined()
  })

  it("forces a stop that is still draining, and keeps following it", async () => {
    const polls = [
      operation({ operation_id: "op-stop", kind: "stop", state: "running" }),
      operation({ operation_id: "op-stop", kind: "stop", state: "running" }),
      operation({ operation_id: "op-stop", kind: "stop", state: "succeeded" }),
    ]
    const { user, clock, caller, onChanged } = await openPage(
      {
        "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      running,
    )

    await confirmStop(user)
    await act(() => clock.advance(0))
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(0))
    expect(screen.getByRole("button", { name: "Force stop" }).hasAttribute("disabled")).toBe(true)
    expect(screen.getByRole("button", { name: /Stop/ }).hasAttribute("disabled")).toBe(true)
    expect(onChanged).not.toHaveBeenCalled()
    await act(() => clock.advance(1_000))

    expect(stops(caller)).toEqual([
      { instance_id: "instance-1" },
      { instance_id: "instance-1", force: true },
    ])
    const followed = caller.calls
      .filter((call) => call.method === "operation.get")
      .map((call) => (call.params as { operation_id: string }).operation_id)
    expect(new Set(followed)).toEqual(new Set(["op-stop"]))
    expect(onChanged).toHaveBeenCalledOnce()
  })
})

describe("removing an instance", () => {
  const running = { ...stopped, intended_state: "running", process: "running" } as const
  const complete = { ...stopped, setup_pending: false }

  it.each([
    ["running", running],
    ["stopped", complete],
    ["stopped with setup pending", stopped],
  ] as const)("offers Remove in the ⋯ menu when it is %s", async (_state, instance) => {
    const { user } = await openPage({}, instance)

    await user.click(screen.getByRole("button", { name: "More actions" }))

    expect(await screen.findByRole("menuitem", { name: "Remove" })).toBeDefined()
  })

  it("asks first, and makes no call when the removal is cancelled", async () => {
    const { user, caller } = await openPage({}, running)

    const dialog = await askToRemove(user)
    expect(dialog.textContent).toContain(
      "Ada stops and leaves the sidebar. Its data stays, and you can restore it from Removed instances.",
    )
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())

    expect(removals(caller)).toEqual([])
  })

  it("removes it once confirmed", async () => {
    const { user, clock, caller } = await openPage(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "remove", state: "running" }),
      },
      complete,
    )

    await confirmRemoval(user)
    await act(() => clock.advance(0))

    expect(removals(caller)).toEqual([{ instance_id: "instance-1" }])
  })

  it("lists the instances again once it is removed, and goes home", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const polls = [
      operation({ kind: "remove", state: "running" }),
      operation({ kind: "remove", state: "succeeded" }),
    ]
    const { user, clock, onChanged } = await openPage(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      running,
    )

    await confirmRemoval(user)
    await act(() => clock.advance(0))
    expect(onChanged).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/instances/instance-1")
    await act(() => clock.advance(1_000))

    expect(onChanged).toHaveBeenCalledOnce()
    expect(window.location.pathname).toBe("/")
  })

  it("says it is removing while the removal drains, and keeps following it once forced", async () => {
    const polls = [
      operation({ operation_id: "op-remove", kind: "remove", state: "running" }),
      operation({ operation_id: "op-remove", kind: "remove", state: "succeeded" }),
    ]
    const { user, clock, caller, onChanged } = await openPage(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        // A force stop escalates the pending removal, and the hub answers with its operation.
        "instance.stop": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      running,
    )

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
    const followed = caller.calls
      .filter((call) => call.method === "operation.get")
      .map((call) => (call.params as { operation_id: string }).operation_id)
    expect(new Set(followed)).toEqual(new Set(["op-remove"]))
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("goes home when the removal ends before its Force stop lands", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const polls = [
      operation({ operation_id: "op-remove", kind: "remove", state: "running" }),
      operation({ operation_id: "op-remove", kind: "remove", state: "succeeded" }),
    ]
    const { user, clock, onChanged } = await openPage(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        "instance.stop": () => {
          throw new CallError({ code: "NOT_FOUND", message: "no such instance", retryable: false })
        },
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      running,
    )

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
    const polls = [
      operation({ operation_id: "op-remove", kind: "remove", state: "running" }),
      operation({ operation_id: "op-remove", kind: "remove", state: "failed", detail }),
    ]
    const { user, clock, caller, onChanged } = await openPage(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        // With the removal over, the hub starts a stop of its own.
        "instance.stop": () => ({ operation_id: "op-stop", instance_id: "instance-1" }),
        "operation.get": (params) =>
          (params as { operation_id: string }).operation_id === "op-stop"
            ? operation({ operation_id: "op-stop", kind: "stop", state: "succeeded" })
            : ((polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult),
      },
      running,
    )

    await confirmRemoval(user)
    await act(() => clock.advance(0))
    await user.click(screen.getByRole("button", { name: "Force stop" }))
    await act(() => clock.advance(1_000))

    expect(within(screen.getByRole("alert")).getByText(detail)).toBeDefined()
    const followed = caller.calls
      .filter((call) => call.method === "operation.get")
      .map((call) => (call.params as { operation_id: string }).operation_id)
    expect(new Set(followed)).toEqual(new Set(["op-remove"]))
    expect(onChanged).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/instances/instance-1")
  })

  it("shows the hub's message in the dialog when the hub refuses the removal", async () => {
    const refusal =
      "The Codex sign-in is still running for this instance. Finish the sign-in or let its code expire first."
    const { user, clock, onChanged } = await openPage(
      {
        "instance.remove": () => {
          throw new CallError({ code: "INSTANCE_BUSY", message: refusal, retryable: false })
        },
      },
      running,
    )

    const dialog = await askToRemove(user)
    await user.click(within(dialog).getByRole("button", { name: "Remove" }))
    await act(() => clock.advance(0))

    expect(within(screen.getByRole("alertdialog")).getByText(refusal)).toBeDefined()
    expect(screen.queryByText("Removing…")).toBeNull()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("says why a removal failed, and offers Remove again", async () => {
    const detail = "The container could not be removed: permission denied."
    const { user, clock, onChanged } = await openPage(
      {
        "instance.remove": () => ({ operation_id: "op-remove", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "remove", state: "failed", detail }),
      },
      complete,
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

const removals = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.remove").map((call) => call.params)

async function confirmStop(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Stop" }))
  const dialog = await screen.findByRole("alertdialog", { name: "Stop Ada?" })
  await user.click(within(dialog).getByRole("button", { name: "Stop" }))
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
}

const stops = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.stop").map((call) => call.params)
