import type {
  Client,
  InstanceSetup,
  InstanceStatusResult,
  InstanceSummary,
  OperationGetResult,
} from "@kinby/contract"
import { type Answers, fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
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
    { name: "api_key", label: "API key", required: true, is_set: true },
    { name: "GH_TOKEN", label: "GitHub token", required: true, is_set: false },
    { name: "WEBHOOK_SECRET", label: "Webhook secret", required: false, is_set: false },
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
