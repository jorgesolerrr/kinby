import type {
  Client,
  InstanceSetup,
  InstanceStatusResult,
  InstanceToolsetResult,
  InstanceSummary,
  OperationGetResult,
  ViewSummary,
} from "@kinby/contract"
import {
  type Answers,
  fakeClock,
  instanceSummary,
  stubCaller,
  stubSubscriber,
} from "@kinby/contract/testing"
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
  missing_commands: [],
}

const stopped = instanceSummary({
  instance_id: "instance-1",
  persona_name: "Ada",
  intended_state: "stopped",
  setup_pending: true,
})

const toolset: InstanceToolsetResult = {
  default_tools: [{ name: "bash", on: false }],
  search: { backend: "brave", variable: "BRAVE_API_KEY", is_set: true },
  mcp_servers: [],
  packages: [],
  authored_tools: [],
  browser: false,
}

function status(fields: Partial<InstanceStatusResult> = {}): InstanceStatusResult {
  return {
    instance_id: "instance-1",
    process: "created",
    readiness: "not-running",
    setup,
    home: false,
    recreate_reasons: [],
    repository_commands: [],
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

async function openPage(answers: Answers, instance = stopped, stopping = false) {
  const caller = {
    ...stubCaller({
      "instance.status": () => status(),
      "instance.toolset.get": () => toolset,
      ...answers,
    }),
    ...stubSubscriber(),
  }
  const clock = fakeClock()
  const onChanged = vi.fn()
  const page = (shown: InstanceSummary) => (
    <InstancePage
      caller={caller}
      clock={clock}
      instance={shown}
      stopping={stopping}
      onChanged={onChanged}
    />
  )
  const { rerender } = render(page(instance))
  await act(() => clock.advance(0))
  // What the page shows once the instances are listed again with `listed` in them.
  const relist = (listed: InstanceSummary) => rerender(page(listed))
  return { caller, clock, onChanged, relist, user: userEvent.setup() }
}

/** The instance's own connection, connected, answering `answers`. */
function reached(answers: Answers) {
  return {
    ...stubCaller(answers),
    state: () => "connected" as const,
    onStateChange: () => () => {},
  }
}

const card = () => within(screen.getByRole("region", { name: "Finish setting up Ada" }))
const login = (name: string) => within(screen.getByRole("listitem", { name }))
const secret = (name: string) =>
  within(within(screen.getByRole("list", { name: "Secrets" })).getByRole("listitem", { name }))

describe("the setup card", () => {
  it("opens a stopped instance with pending setup on each login and secret", async () => {
    await openPage({})

    expect(card().queryByRole("button", { name: "Start" })).toBeNull()
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

  it("names each missing list of repository commands, and says to ask for a survey", async () => {
    const missing: InstanceSetup = { ...setup, missing_commands: ["setup", "checks"] }
    await openPage({ "instance.status": () => status({ setup: missing }) })

    const lists = within(card().getByRole("list", { name: "Repository commands" }))
    expect(lists.getByRole("listitem", { name: "setup" })).toBeDefined()
    expect(lists.getByRole("listitem", { name: "checks" })).toBeDefined()
    expect(lists.getAllByText("Missing")).toHaveLength(2)
    expect(
      card().getByText(/Start Ada and ask it in a thread for a repository survey/),
    ).toBeDefined()
  })

  it("shows the lists of repository commands it holds beside a missing one", async () => {
    await openPage({
      "instance.status": () =>
        status({
          setup: { ...setup, missing_commands: ["checks"] },
          repository_commands: [{ name: "setup", commands: ["uv sync"] }],
        }),
    })

    const lists = within(card().getByRole("list", { name: "Repository commands" }))
    expect(
      within(lists.getByRole("listitem", { name: "setup" })).getByText("uv sync"),
    ).toBeDefined()
    expect(
      within(lists.getByRole("listitem", { name: "checks" })).getByText("Missing"),
    ).toBeDefined()
  })

  it("shows no repository commands when none is missing", async () => {
    await openPage({})

    expect(card().queryByRole("heading", { name: "Repository commands" })).toBeNull()
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

  it("fills a secret from a picked hub secret, then names the one it takes", async () => {
    let linked = false
    const { user, clock, caller, relist } = await openPage({
      "instance.status": () =>
        status({
          setup: {
            ...setup,
            secrets: setup.secrets.map((each) =>
              linked && each.name === "GH_TOKEN"
                ? { ...each, is_set: true, hub_secret: "GITHUB_TOKEN" }
                : each,
            ),
          },
        }),
      "hub.secret.list": () => ({ secrets: [{ name: "GITHUB_TOKEN" }] }),
      "instance.secrets.set": () => {
        linked = true
        return { operation_id: "op-secret", instance_id: "instance-1" }
      },
      "operation.get": () => operation({ kind: "secrets", state: "succeeded" }),
    })

    await user.click(secret("GitHub token").getByRole("button", { name: "Set" }))
    await user.click(
      secret("GitHub token").getByRole("combobox", { name: "Hub secret for GitHub token" }),
    )
    await user.click(await screen.findByRole("option", { name: "GITHUB_TOKEN" }))
    await user.click(secret("GitHub token").getByRole("button", { name: "Save" }))
    await act(() => clock.advance(0))

    expect(caller.calls.find((call) => call.method === "instance.secrets.set")?.params).toEqual({
      instance_id: "instance-1",
      hub_secrets: { GH_TOKEN: "GITHUB_TOKEN" },
    })
    relist({ ...stopped })
    await act(() => clock.advance(0))
    expect(secret("GitHub token").getByText("GITHUB_TOKEN")).toBeDefined()
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
})

describe("a stopped instance with setup complete", () => {
  const complete = { ...stopped, setup_pending: false }
  const stoppedView = () => within(screen.getByRole("region", { name: "Ada is stopped" }))

  it("shows it stopped instead of the setup card, with no Start of its own", async () => {
    await openPage({}, complete)

    expect(stoppedView().getByText("It does nothing until it starts.")).toBeDefined()
    expect(stoppedView().queryByRole("button", { name: "Start" })).toBeNull()
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

  it("leaves the stopped view once it is listed again running", async () => {
    const { clock, relist } = await openPage({}, complete)

    relist({ ...complete, intended_state: "running" })
    await act(() => clock.advance(0))

    expect(screen.queryByRole("region", { name: "Ada is stopped" })).toBeNull()
  })
})

describe("what needs the user on a running instance", () => {
  const running = { ...stopped, intended_state: "running", process: "running" } as const
  const needsYou = () => within(screen.getByRole("region", { name: "Needs you" }))

  it("lists the logins not signed in or failed with Sign in, without the setup card", async () => {
    const logins: InstanceSetup["logins"] = [
      { ...setup.logins[0]!, state: "pending" },
      { ...setup.logins[1]!, state: "failed" },
      { id: "claude", label: "Claude", description: "Signs Claude in.", state: "signed_in" },
    ]
    await openPage({ "instance.status": () => status({ setup: { ...setup, logins } }) }, running)

    expect(screen.queryByRole("region", { name: "Finish setting up Ada" })).toBeNull()
    expect(login("Codex").getByText("Not signed in")).toBeDefined()
    expect(login("Codex").getByRole("button", { name: "Sign in" })).toBeDefined()
    expect(login("Editor account").getByText("Failed")).toBeDefined()
    expect(needsYou().queryByRole("listitem", { name: "Claude" })).toBeNull()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("signs in from its row, and lists the instances again once it ends", async () => {
    const polls = [waitingForCode, operation({ state: "succeeded", detail: "Signed in." })]
    const { user, clock, caller, onChanged } = await openPage(
      {
        "instance.status": () => status({ process: "running" }),
        "instance.login.start": () => ({ operation_id: "op-1", instance_id: "instance-1" }),
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      },
      running,
    )

    await user.click(login("Codex").getByRole("button", { name: "Sign in" }))
    await act(() => clock.advance(0))
    expect(login("Codex").getByText("ABCD-12345")).toBeDefined()
    await act(() => clock.advance(1_000))

    expect(caller.calls.find((call) => call.method === "instance.login.start")?.params).toEqual({
      instance_id: "instance-1",
      login_id: "codex",
    })
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it("follows a sign-in that runs when the page loads", async () => {
    const signingIn: InstanceSetup = {
      ...setup,
      logins: [{ ...setup.logins[0]!, operation_id: "op-1" }, setup.logins[1]!],
    }
    await openPage(
      {
        "instance.status": () => status({ process: "running", setup: signingIn }),
        "operation.get": () => waitingForCode,
      },
      running,
    )

    expect(login("Codex").getByText("ABCD-12345")).toBeDefined()
    expect(login("Codex").getByText("Waiting")).toBeDefined()
  })

  it("lists each MCP server that failed, and opens Config on Skills for it", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const failed: InstanceSummary = {
      ...running,
      notices: [
        { code: "mcp_server_failed", server: "fetch", message: "fetch exited with code 1." },
        { code: "template_behind", message: "The template changed." },
      ],
    }
    const { user } = await openPage(
      {
        "instance.status": () => status({ process: "running", setup: { ...setup, logins: [] } }),
      },
      failed,
    )

    const server = within(needsYou().getByRole("listitem", { name: "MCP server fetch failed" }))
    expect(server.getByText("fetch exited with code 1.")).toBeDefined()
    expect(needsYou().queryByText("The template changed.")).toBeNull()
    expect(screen.queryByText(/^Start a thread with/)).toBeNull()
    await user.click(server.getByRole("button", { name: "Open Skills" }))

    expect(window.location.pathname).toBe("/instances/instance-1/config/skills")
  })

  // jsdom lays nothing out, so the layout shows only in the classes.
  it("gives a login's description the row's width on a phone, above its badge and button", async () => {
    await openPage({ "instance.status": () => status({ process: "running" }) }, running)

    const description = login("Codex").getByText("Signs Codex in with your ChatGPT plan.")
    expect(description.className).not.toMatch(/line-clamp-\d/)
    const text = description.parentElement?.classList
    expect(text?.contains("basis-full") && text.contains("sm:basis-0")).toBe(true)
  })

  it("is absent when every login is signed in, and says nothing needs the user", async () => {
    const logins = setup.logins.map((login) => ({ ...login, state: "signed_in" as const }))
    await openPage({ "instance.status": () => status({ setup: { ...setup, logins } }) }, running)

    expect(screen.queryByRole("region", { name: "Needs you" })).toBeNull()
    expect(screen.getByText("Nothing needs you")).toBeDefined()
    expect(screen.getByText(/^Start a thread with New thread above Ada's threads/)).toBeDefined()
  })

  it("says nothing needs the user when the status cannot be read", async () => {
    const caller: Pick<Client, "call" | "subscribe"> = {
      call: () => Promise.reject(new Error("gone")),
      ...stubSubscriber(),
    }
    const clock = fakeClock()
    render(<InstancePage caller={caller} clock={clock} instance={running} onChanged={vi.fn()} />)
    await act(() => clock.advance(0))

    expect(screen.getByText(/^Start a thread with/)).toBeDefined()
    expect(screen.queryByRole("region", { name: "Needs you" })).toBeNull()
  })

  it("does not say nothing needs the user before the status is read", () => {
    const caller: Pick<Client, "call" | "subscribe"> = {
      call: () => new Promise<never>(() => {}),
      ...stubSubscriber(),
    }
    render(
      <InstancePage caller={caller} clock={fakeClock()} instance={running} onChanged={vi.fn()} />,
    )

    expect(screen.queryByText(/^Start a thread with/)).toBeNull()
    expect(screen.queryByRole("region", { name: "Needs you" })).toBeNull()
  })
})

describe("the views of a running instance", () => {
  const running = { ...stopped, intended_state: "running", process: "running" } as const

  async function openWithViews(views: ViewSummary[]) {
    const caller = {
      ...stubCaller({
        "instance.status": () => status({ process: "running" }),
        "instance.toolset.get": () => toolset,
      }),
      ...stubSubscriber(),
    }
    const client = reached({ "view.list": () => ({ views }) })
    const clock = fakeClock()
    render(
      <InstancePage
        caller={caller}
        client={client}
        clock={clock}
        instance={running}
        onChanged={vi.fn()}
      />,
    )
    await act(() => clock.advance(0))
    return {
      clock,
      section: within(screen.getByRole("region", { name: "Views" })),
      user: userEvent.setup(),
    }
  }

  it("lists the instance's views as rows, and opens the one picked", async () => {
    window.history.replaceState(null, "", "/instances/instance-1")
    const { section, user } = await openWithViews([
      { id: "applications", title: "Job board", updated_at: "2026-10-25T09:30:00Z" },
      { id: "reading", title: "Reading list", updated_at: "2026-10-24T08:00:00Z" },
    ])

    const rows = within(section.getByRole("list", { name: "Views" })).getAllByRole("listitem")
    expect(rows.map((row) => row.textContent)).toEqual(["Job board", "Reading list"])
    await user.click(section.getByRole("button", { name: "Job board" }))

    expect(window.location.pathname).toBe("/instances/instance-1/views/applications")
  })

  it("says when the instance has no views yet", async () => {
    const { section } = await openWithViews([])

    expect(section.getByText("No views yet.")).toBeDefined()
  })

  it("lists a view the instance makes while Overview is open", async () => {
    const views: ViewSummary[] = []
    const { clock, section } = await openWithViews(views)

    views.push({ id: "applications", title: "Job board", updated_at: "2026-10-25T09:30:00Z" })
    await act(() => clock.advance(5_000))

    expect(section.getByRole("button", { name: "Job board" })).toBeDefined()
  })
})

describe("the repository commands", () => {
  const running = { ...stopped, intended_state: "running", setup_pending: false } as const
  const held = status({
    process: "running",
    setup: { ...setup, logins: [], missing_commands: ["deploy"] },
    repository_commands: [
      { name: "setup", commands: [] },
      { name: "checks", commands: ["uv run ruff check .", "uv run pytest"] },
    ],
  })
  const section = () => within(screen.getByRole("region", { name: "Repository commands" }))
  const list = (name: string) => within(section().getByRole("listitem", { name }))

  it("shows each list by name with its commands in order, an empty one and a missing one", async () => {
    await openPage({ "instance.status": () => held }, running)

    expect(
      list("checks")
        .getAllByRole("listitem")
        .map((command) => command.textContent),
    ).toEqual(["uv run ruff check .", "uv run pytest"])
    expect(list("setup").getByText("Empty")).toBeDefined()
    expect(list("deploy").getByText("Missing")).toBeDefined()
    expect(screen.queryByText(/^Start a thread with/)).toBeNull()
  })

  it("offers no way to edit a list, and says to ask the instance", async () => {
    await openPage({ "instance.status": () => held }, running)

    expect(section().queryByRole("button")).toBeNull()
    expect(section().queryByRole("textbox")).toBeNull()
    expect(section().getByText(/To change them, ask Ada in a thread/)).toBeDefined()
  })

  it("shows them on a stopped instance too", async () => {
    await openPage({ "instance.status": () => held }, { ...running, intended_state: "stopped" })

    expect(list("checks").getByText("uv run pytest")).toBeDefined()
  })

  it("shows no section when its factory role needs no list", async () => {
    await openPage({ "instance.status": () => status({ process: "running" }) }, running)

    expect(screen.queryByRole("region", { name: "Repository commands" })).toBeNull()
  })
})

describe("what the header holds", () => {
  const running = { ...stopped, intended_state: "running", process: "running" } as const

  it.each([
    ["running", running],
    ["stopped", { ...stopped, setup_pending: false }],
    ["stopped with setup pending", stopped],
  ] as const)("draws no name, Start, Stop or ⋯ menu when it is %s", async (_state, instance) => {
    await openPage({}, instance)

    expect(screen.queryByRole("heading", { name: "Ada" })).toBeNull()
    expect(screen.queryByRole("button", { name: /^(Start|Stop)$/ })).toBeNull()
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull()
  })

  it("has no Configure, Memory, Stats or Views button of its own", async () => {
    await openPage({ "instance.status": () => status({ process: "running" }) }, running)

    for (const name of ["Configure", "Memory", "Stats", "Views"]) {
      expect(screen.queryByRole("button", { name })).toBeNull()
    }
  })

  it("keeps the running view while a stop asked from the header drains", async () => {
    await openPage(
      { "instance.status": () => status({ process: "running" }) },
      { ...running, intended_state: "stopped" },
      true,
    )

    expect(screen.queryByRole("region", { name: "Ada is stopped" })).toBeNull()
    expect(login("Codex").getByRole("button", { name: "Sign in" })).toBeDefined()
  })
})

describe("the browser profiles and the vault of an instance", () => {
  const running = { ...stopped, intended_state: "running", process: "running" } as const

  function openWithBrowser(browser: boolean, instance: InstanceSummary = running) {
    const caller = {
      ...stubCaller({
        "instance.status": () => status({ process: instance.process, browser }),
        "instance.toolset.get": () => toolset,
        "browser.profile.list": () => ({
          profiles: [{ name: "LinkedIn", site: "www.linkedin.com" }],
        }),
        "browser.vault.list": () => ({
          entries: [{ name: "Indeed", site: "https://secure.indeed.com" }],
        }),
      }),
      ...stubSubscriber(),
    }
    render(
      <InstancePage
        caller={caller}
        client={reached({})}
        clock={fakeClock()}
        instance={instance}
        onChanged={vi.fn()}
      />,
    )
  }

  it("lists them on an instance the hub opens browsers for", async () => {
    openWithBrowser(true)

    const profiles = await screen.findByRole("list", { name: "Browser profiles" })

    expect(within(profiles).getByRole("listitem", { name: "LinkedIn" })).toBeDefined()
  })

  it("lists the vault on an instance the hub opens browsers for", async () => {
    openWithBrowser(true)

    const vault = await screen.findByRole("list", { name: "Vault" })

    expect(within(vault).getByRole("listitem", { name: "Indeed" })).toBeDefined()
  })

  it("lists them on a stopped instance too, since the hub keeps its browser", async () => {
    openWithBrowser(true, { ...stopped, setup_pending: false })

    const profiles = await screen.findByRole("list", { name: "Browser profiles" })

    expect(within(profiles).getByRole("listitem", { name: "LinkedIn" })).toBeDefined()
  })

  it("leaves them out on an instance with the browser off", async () => {
    openWithBrowser(false)

    await screen.findByRole("list", { name: "Toolset" })

    expect(screen.queryByRole("heading", { name: "Browser profiles" })).toBeNull()
    expect(screen.queryByRole("heading", { name: "Vault" })).toBeNull()
  })
})

describe("the row groups of Overview", () => {
  it("sits each section's rows in one bordered group, Needs you's logins and failed servers too", async () => {
    const caller = {
      ...stubCaller({
        "instance.status": () =>
          status({
            process: "running",
            browser: true,
            setup: { ...setup, missing_commands: ["deploy"] },
            repository_commands: [{ name: "checks", commands: ["uv run pytest"] }],
          }),
        "instance.toolset.get": () => toolset,
        "browser.profile.list": () => ({
          profiles: [{ name: "LinkedIn", site: "www.linkedin.com" }],
        }),
        "browser.vault.list": () => ({
          entries: [{ name: "Indeed", site: "https://secure.indeed.com" }],
        }),
      }),
      ...stubSubscriber(),
    }
    const client = reached({
      "view.list": () => ({
        views: [{ id: "applications", title: "Job board", updated_at: "2026-10-25T09:30:00Z" }],
      }),
    })
    const instance: InstanceSummary = {
      ...stopped,
      intended_state: "running",
      process: "running",
      notices: [{ code: "mcp_server_failed", server: "fetch", message: "fetch exited." }],
    }
    render(
      <InstancePage
        caller={caller}
        client={client}
        clock={fakeClock()}
        instance={instance}
        onChanged={vi.fn()}
      />,
    )

    const needsYou = await within(
      await screen.findByRole("region", { name: "Needs you" }),
    ).findByRole("list")
    expect(within(needsYou).getByRole("listitem", { name: "Codex" })).toBeDefined()
    expect(
      within(needsYou).getByRole("listitem", { name: "MCP server fetch failed" }),
    ).toBeDefined()
    const lists = [needsYou]
    for (const name of ["Views", "Browser profiles", "Vault", "Toolset", "Repository commands"]) {
      lists.push(await screen.findByRole("list", { name }))
    }
    for (const list of lists) {
      expect(list.getAttribute("data-variant")).toBe("outline")
      const rows = list.querySelectorAll(":scope > li > [data-slot=item]")
      expect(rows.length).toBe(list.querySelectorAll(":scope > li").length)
      for (const row of rows) expect(row.getAttribute("data-size")).toBe("sm")
    }
  })
})

describe("the toolset of an instance", () => {
  const running = { ...stopped, intended_state: "running", process: "running" } as const

  async function openWithToolset(instance: InstanceSummary) {
    const caller = {
      ...stubCaller({
        "instance.status": () => status({ process: instance.process }),
        "instance.toolset.get": () => toolset,
      }),
      ...stubSubscriber(),
    }
    render(
      <InstancePage
        caller={caller}
        client={reached({})}
        clock={fakeClock()}
        instance={instance}
        onChanged={vi.fn()}
      />,
    )
    return screen.findByRole("list", { name: "Toolset" })
  }

  it("shows it on a running instance", async () => {
    const toolset = await openWithToolset(running)

    expect(within(toolset).getByText("Off: bash")).toBeDefined()
  })

  it("shows it on a stopped instance too, since the hub reads it from the instance's files", async () => {
    const toolset = await openWithToolset({ ...stopped, setup_pending: false })

    expect(within(toolset).getByText("Off: bash")).toBeDefined()
  })
})
