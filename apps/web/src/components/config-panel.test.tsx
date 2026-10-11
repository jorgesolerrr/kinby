import { CallError } from "@kinby/contract"
import type {
  ConfigChange,
  InstanceSetup,
  InstanceStatusResult,
  InstanceSummary,
  OperationGetResult,
  PermissionsResult,
  PermissionsSetCommand,
  PromptResult,
} from "@kinby/contract"
import { type Answers, fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ConfigPanel } from "@/components/config-panel"

const behavior: PromptResult = { content: "Be brief.\n", hash: "hash-1", default: false }

const byTheAgent: ConfigChange = {
  // Local time, so the page shows the same clock time wherever the test runs.
  at: new Date(2026, 8, 28, 10, 4).toISOString(),
  file: "SYSTEM.md",
  actor: "agent",
  thread_id: "thread-1",
  turn_id: "turn-1",
  diff: "",
}

const setup: InstanceSetup = {
  logins: [
    {
      id: "claude",
      label: "Claude Code",
      description: "Signs Claude Code in with your plan.",
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
  ],
}

function status(fields: Partial<InstanceStatusResult> = {}): InstanceStatusResult {
  return {
    instance_id: "instance-1",
    process: "running",
    readiness: "ready",
    setup,
    recreate_reasons: [],
    ...fields,
  }
}

function operation(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-1",
    instance_id: "instance-1",
    kind: "recreate",
    state: "running",
    detail: "",
    steps: [],
    ...fields,
  }
}

const ada = instanceSummary({ instance_id: "instance-1" })

const behind = instanceSummary({
  instance_id: "instance-1",
  source_revision: "a".repeat(40),
  notices: [
    {
      code: "revision_behind",
      message: "The instance runs kinby aaaaaaa, and the hub is at bbbbbbb.",
      instance_revision: "a".repeat(40),
      hub_revision: "b".repeat(40),
    },
  ],
})

const methodNotFound = (method: string): never => {
  throw new CallError({
    code: "NOT_FOUND",
    message: `Method "${method}" was not found.`,
    retryable: false,
  })
}

const notConnected = () =>
  new CallError({
    code: "CONNECTION_LOST",
    message: "Not connected to the hub. The call was not sent.",
    retryable: true,
  })

/** The panel, with one stub answering both the instance's calls and the hub's. */
function openPanel(answers: Answers, instance: InstanceSummary = ada) {
  const caller = stubCaller({
    "prompt.get": ({ name }) =>
      name === "behavior"
        ? behavior
        : { content: "The shipped lens.", hash: "empty", default: true },
    "config.history": ({ file }) => ({ changes: file === "SYSTEM.md" ? [byTheAgent] : [] }),
    "instance.status": () => status(),
    ...answers,
  })
  const clock = fakeClock()
  render(
    <ConfigPanel
      client={caller}
      caller={caller}
      clock={clock}
      instance={instance}
      onChanged={() => {}}
    />,
  )
  return { caller, clock, user: userEvent.setup() }
}

const section = (name: string) =>
  within(screen.getByRole("navigation", { name: "Config sections" })).getByRole("button", {
    name,
  })

// jsdom applies no Tailwind, so the classes tell whether the browser draws a marker: an `Item`
// rendered as the `<li>` lays out as flex, and any other `<li>` needs `list-none`.
const marked = (item: HTMLElement) =>
  !item.classList.contains("flex") && !item.classList.contains("list-none")

describe("ConfigPanel", () => {
  it("left-aligns every line of a section row, since a button centers its text", () => {
    openPanel({})

    const sections = screen.getByRole("navigation", { name: "Config sections" })
    // jsdom applies no Tailwind, so the class stands in for the computed alignment.
    expect(
      within(sections)
        .getAllByRole("button")
        .every((button) => button.classList.contains("text-left")),
    ).toBe(true)
  })

  it("lists every section in its group, and the ones not built yet as unavailable", async () => {
    openPanel({})

    const sections = screen.getByRole("navigation", { name: "Config sections" })
    const group = (name: string) => within(sections).getByRole("list", { name })
    const labels = (name: string) =>
      within(group(name))
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label"))
    expect(within(sections).getAllByRole("list")).toEqual(
      ["Behavior", "Capabilities", "Instance"].map(group),
    )
    expect(labels("Behavior")).toEqual([
      "Behavior prompt",
      "Recap prompt",
      "Permissions",
      "Manifest",
    ])
    expect(labels("Capabilities")).toEqual(["Routines", "Skills", "Tools"])
    expect(labels("Instance")).toEqual(["Secrets and login", "Core version"])
    expect(screen.getByRole("button", { name: "Behavior prompt" })).toHaveProperty(
      "disabled",
      false,
    )
    expect(screen.getByRole("button", { name: "Recap prompt" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Skills" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Tools" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Routines" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Permissions" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Manifest" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Core version" })).toHaveProperty("disabled", false)
    expect(await screen.findByRole("heading", { name: "Behavior prompt" })).toBeDefined()
  })

  it("shows no list marker beside a section", async () => {
    openPanel({})

    const sections = screen.getByRole("navigation", { name: "Config sections" })
    expect(within(sections).getAllByRole("listitem").filter(marked)).toEqual([])
    expect(await screen.findByRole("heading", { name: "Behavior prompt" })).toBeDefined()
  })

  it("says the core section is behind the hub in place of its hint", async () => {
    const section = () => screen.getByRole("button", { name: "Core version" })

    openPanel({}, ada)
    expect(section().textContent).toContain("the kinby it runs, update")
    cleanup()
    const { user } = openPanel({}, behind)

    expect(section().textContent).toContain("behind the hub")
    expect(section().textContent).not.toContain("the kinby it runs, update")
    await user.click(section())
    expect(await screen.findByRole("heading", { name: "Core version" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Update core" })).toBeDefined()
  })

  it("reads the first section again once the socket connects, with no error in between", async () => {
    let connected = false
    const { clock } = openPanel({
      "prompt.get": () => {
        if (!connected) throw notConnected()
        return behavior
      },
    })

    await act(() => clock.advance(0))
    expect(screen.queryByRole("alert")).toBeNull()
    connected = true
    await act(() => clock.advance(1_000))

    expect(await screen.findByRole("textbox", { name: "SYSTEM.md" })).toHaveProperty(
      "value",
      "Be brief.\n",
    )
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("says the instance did not answer a save the dropped connection lost, and does not send it again", async () => {
    const { caller, clock, user } = openPanel({
      "prompt.set": () => {
        throw notConnected()
      },
    })

    await user.type(await screen.findByRole("textbox", { name: "SYSTEM.md" }), "Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    await act(() => clock.advance(1_000))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("The instance did not answer")
    expect(alert.textContent).toContain("Not connected to the hub. The call was not sent.")
    expect(caller.calls.filter((call) => call.method === "prompt.set")).toHaveLength(1)
  })

  it("says an instance behind the hub runs an older core without the section, and opens the update", async () => {
    const { user } = openPanel({ "prompt.get": () => methodNotFound("prompt.get") }, behind)

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("This instance runs an older core")
    expect(alert.textContent).not.toContain('Method "prompt.get" was not found.')
    await user.click(within(alert).getByRole("button", { name: "Open Core version" }))

    expect(await screen.findByRole("heading", { name: "Core version" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Update core" })).toBeDefined()
  })

  it("shows the instance's own not found message when the instance is not behind", async () => {
    openPanel({ "prompt.get": () => methodNotFound("prompt.get") })

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("The instance refused it")
    expect(alert.textContent).toContain('Method "prompt.get" was not found.')
    expect(alert.textContent).not.toContain("older core")
  })

  it("edits the behavior prompt and says who changed it last", async () => {
    const { caller, user } = openPanel({
      "prompt.set": ({ content }) => ({ content, hash: "hash-2", default: false }),
    })

    const editor = await screen.findByRole("textbox", { name: "SYSTEM.md" })
    expect(editor).toHaveProperty("value", "Be brief.\n")
    expect(screen.getByText("Last changed by the agent, Sep 28, 2026, 10:04 AM")).toBeDefined()
    await user.clear(editor)
    await user.type(editor, "Be kind.")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Saved. It applies at the next turn.")).toBeDefined()
    expect(caller.calls.filter((call) => call.method === "prompt.set")).toEqual([
      {
        method: "prompt.set",
        params: { name: "behavior", content: "Be kind.", hash: "hash-1" },
      },
    ])
    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true)
  })

  it("shows when the recap prompt is the shipped default", async () => {
    const { user } = openPanel({})

    await user.click(screen.getByRole("button", { name: "Recap prompt" }))

    expect(await screen.findByRole("textbox", { name: "RECAP.md" })).toHaveProperty(
      "value",
      "The shipped lens.",
    )
    expect(screen.getByText("Shipped default")).toBeDefined()
    expect(screen.getByText("Never changed")).toBeDefined()
  })

  it("offers to load theirs when the prompt changed since it was opened", async () => {
    let theirs = false
    const { caller, user } = openPanel({
      "prompt.get": () =>
        theirs ? { content: "The agent's text.", hash: "hash-3", default: false } : behavior,
      "prompt.set": () => {
        theirs = true
        throw new CallError({ code: "STALE", message: "SYSTEM.md changed.", retryable: false })
      },
    })

    const editor = await screen.findByRole("textbox", { name: "SYSTEM.md" })
    await user.type(editor, "Mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

    expect(await screen.findByDisplayValue("The agent's text.")).toBe(editor)
    expect(screen.queryByRole("alert")).toBeNull()
    await user.type(editor, " And mine.")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(caller.calls.filter((call) => call.method === "prompt.set").at(-1)?.params).toEqual({
      name: "behavior",
      content: "The agent's text. And mine.",
      hash: "hash-3",
    })
  })

  it("opens the skills and the tools", async () => {
    const { user } = openPanel({
      "skill.list": () => ({
        skills: [
          {
            name: "planning",
            tier: "kinby",
            description: "Plan work.",
            shadowed_by: null,
          },
        ],
        warnings: [],
      }),
      "tool.list": () => ({
        tools: [{ name: "skill", source: "core", write: false, rule: "mode" }],
        warnings: [],
      }),
    })

    await user.click(screen.getByRole("button", { name: "Skills" }))
    expect(await screen.findByRole("heading", { name: "Skills" })).toBeDefined()
    expect(await screen.findByRole("button", { name: "planning, kinby skill" })).toBeDefined()
    await user.click(screen.getByRole("button", { name: "Tools" }))
    expect(await screen.findByRole("table", { name: "Tools" })).toBeDefined()
    await user.click(screen.getByRole("button", { name: "Change a rule in Permissions" }))
    expect(await screen.findByRole("heading", { name: "Permissions" })).toBeDefined()
  })

  it("opens the routine a link names in the routines section's editor", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/config/routines/inbox")
    try {
      openPanel({
        "routine.list": () => ({ routines: [], warnings: [] }),
        "routine.read": ({ name }) => ({ name, content: "Check the inbox.\n", hash: "hash-1" }),
      })

      expect(await screen.findByRole("heading", { name: "Routines" })).toBeDefined()
      expect(await screen.findByDisplayValue("Check the inbox.")).toBeDefined()
    } finally {
      window.history.replaceState(null, "", "/")
    }
  })

  it("drops the routine from the URL when another section opens", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/config/routines/inbox")
    try {
      const { user } = openPanel({
        "routine.list": () => ({ routines: [], warnings: [] }),
        "routine.read": ({ name }) => ({ name, content: "Check the inbox.\n", hash: "hash-1" }),
      })
      await screen.findByDisplayValue("Check the inbox.")

      await user.click(screen.getByRole("button", { name: "Tools" }))

      expect(window.location.pathname).toBe("/instances/instance-1/config")
    } finally {
      window.history.replaceState(null, "", "/")
    }
  })

  it("opens on Core version when a link names it, and drops it from the URL on leaving", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/config/core")
    try {
      const { user } = openPanel({}, behind)

      expect(await screen.findByRole("heading", { name: "Core version" })).toBeDefined()
      expect(screen.getByRole("button", { name: "Update core" })).toBeDefined()
      await user.click(screen.getByRole("button", { name: "Recap prompt" }))

      expect(window.location.pathname).toBe("/instances/instance-1/config")
    } finally {
      window.history.replaceState(null, "", "/")
    }
  })

  it("opens the routines section", async () => {
    const { user } = openPanel({ "routine.list": () => ({ routines: [], warnings: [] }) })

    await user.click(screen.getByRole("button", { name: "Routines" }))

    expect(await screen.findByRole("heading", { name: "Routines" })).toBeDefined()
    expect(await screen.findByRole("button", { name: "New routine" })).toBeDefined()
  })

  describe("Permissions", () => {
    const SHIPPED = ["rm -rf /instance", "git reset --hard", "git push --force"]
    const permissions: PermissionsResult = {
      mode: "auto",
      ceiling: "full-access",
      tools: { bash: "deny" },
      bash: {
        deny: [
          ...SHIPPED.map((pattern) => ({ pattern, shipped: true })),
          { pattern: "^deploy$", shipped: false },
        ],
        ask: [],
      },
      hash: "perm-1",
    }

    async function openPermissions(answers: Answers) {
      const opened = openPanel({ "permissions.get": () => permissions, ...answers })
      await opened.user.click(screen.getByRole("button", { name: "Permissions" }))
      await screen.findByRole("heading", { name: "Permissions" })
      return opened
    }

    const toggle = (group: string, name: string) =>
      within(screen.getByRole("group", { name: group })).getByRole("button", { name })
    const sent = (caller: ReturnType<typeof openPanel>["caller"]) =>
      caller.calls
        .filter((call) => call.method === "permissions.set")
        .map((call) => call.params as PermissionsSetCommand)

    it("disables modes above the ceiling, and lowers the mode when the ceiling drops", async () => {
      const { caller, user } = await openPermissions({
        "permissions.set": () => ({ ...permissions, hash: "perm-2" }),
      })

      expect(await screen.findByRole("group", { name: "Ceiling" })).toBeDefined()
      expect(toggle("Default mode", "auto").getAttribute("aria-pressed")).toBe("true")
      expect(toggle("Default mode", "full-access")).toHaveProperty("disabled", false)
      await user.click(toggle("Ceiling", "ask"))

      expect(toggle("Default mode", "ask").getAttribute("aria-pressed")).toBe("true")
      expect(toggle("Default mode", "auto")).toHaveProperty("disabled", true)
      expect(toggle("Default mode", "full-access")).toHaveProperty("disabled", true)
      expect(
        screen.getByText("Lowered the default mode to ask to stay within the ceiling."),
      ).toBeDefined()
      await user.click(screen.getByRole("button", { name: "Save" }))

      expect(await screen.findByText("Saved. It applies at the next turn.")).toBeDefined()
      expect(sent(caller)).toEqual([
        {
          mode: "ask",
          ceiling: "ask",
          tools: { bash: "deny" },
          bash: { deny: ["^deploy$"], ask: [] },
          hash: "perm-1",
        },
      ])
    })

    it("sets each tool to follow the mode or to allow, ask, or deny", async () => {
      const { caller, user } = await openPermissions({
        "permissions.set": () => ({ ...permissions, hash: "perm-2" }),
      })

      await screen.findByRole("group", { name: "bash" })
      expect(toggle("bash", "deny").getAttribute("aria-pressed")).toBe("true")
      await user.click(toggle("bash", "Follow mode"))
      await user.type(screen.getByRole("textbox", { name: "Tool" }), "web_fetch")
      await user.click(screen.getByRole("button", { name: "Add rule" }))
      expect(toggle("web_fetch", "Follow mode").getAttribute("aria-pressed")).toBe("true")
      await user.click(toggle("web_fetch", "ask"))
      await user.click(screen.getByRole("button", { name: "Save" }))

      await screen.findByText("Saved. It applies at the next turn.")
      expect(sent(caller).map((params) => params.tools)).toEqual([{ web_fetch: "ask" }])
    })

    it("shows the shipped deny patterns locked, and edits the instance's own", async () => {
      const { caller, user } = await openPermissions({
        "permissions.set": () => ({ ...permissions, hash: "perm-2" }),
      })

      const deny = await screen.findByRole("list", { name: "Always denied" })
      for (const pattern of SHIPPED) {
        const item = within(deny).getByText(pattern).closest("li")
        expect(item?.textContent).toContain("Shipped")
        expect(within(item as HTMLElement).queryByRole("button")).toBeNull()
      }
      await user.click(within(deny).getByRole("button", { name: "Remove ^deploy$" }))
      await user.type(screen.getByRole("textbox", { name: "New denied pattern" }), "^drop table")
      await user.click(screen.getByRole("button", { name: "Add denied pattern" }))
      await user.type(screen.getByRole("textbox", { name: "New asked pattern" }), "^npm publish")
      await user.click(screen.getByRole("button", { name: "Add asked pattern" }))
      await user.click(screen.getByRole("button", { name: "Save" }))

      await screen.findByText("Saved. It applies at the next turn.")
      expect(sent(caller).map((params) => params.bash)).toEqual([
        { deny: ["^drop table"], ask: ["^npm publish"] },
      ])
    })

    it("shows why the instance refused a pattern", async () => {
      const { user } = await openPermissions({
        "permissions.set": () => {
          throw new CallError({
            code: "INVALID_ARGUMENT",
            message: "Some values are invalid.",
            retryable: false,
            fields: { "bash.deny.1": "invalid regex: missing ), unterminated subpattern" },
          })
        },
      })

      await user.type(await screen.findByRole("textbox", { name: "New denied pattern" }), "(oops")
      await user.click(screen.getByRole("button", { name: "Add denied pattern" }))
      await user.click(screen.getByRole("button", { name: "Save" }))

      expect(
        await screen.findByText("(oops: invalid regex: missing ), unterminated subpattern"),
      ).toBeDefined()
    })

    it("shows no list marker beside a tool rule or a pattern", async () => {
      await openPermissions({})

      const bash = await screen.findByRole("group", { name: "bash" })
      const deny = screen.getByRole("list", { name: "Always denied" })
      const items = [bash.closest("li"), ...within(deny).getAllByRole("listitem")]
      expect(items).toHaveLength(SHIPPED.length + 2)
      expect(items.filter((item) => item === null || marked(item))).toEqual([])
    })

    it("offers to load theirs when the permissions changed since they were opened", async () => {
      let theirs = false
      const { caller, user } = await openPermissions({
        "permissions.get": () =>
          theirs ? { ...permissions, mode: "ask", hash: "perm-3" } : permissions,
        "permissions.set": () => {
          theirs = true
          throw new CallError({ code: "STALE", message: "changed", retryable: false })
        },
      })

      await screen.findByRole("group", { name: "Default mode" })
      await user.click(toggle("Default mode", "read-only"))
      await user.click(screen.getByRole("button", { name: "Save" }))
      const alert = await screen.findByRole("alert")
      expect(alert.textContent).toContain("Changed since you opened it")
      await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

      await waitFor(() =>
        expect(toggle("Default mode", "ask").getAttribute("aria-pressed")).toBe("true"),
      )
      expect(screen.queryByRole("alert")).toBeNull()
      await user.click(toggle("Default mode", "auto"))
      await user.click(screen.getByRole("button", { name: "Save" }))
      expect(sent(caller).at(-1)?.hash).toBe("perm-3")
    })
  })

  describe("Secrets and login", () => {
    async function openSecrets(answers: Answers) {
      const opened = openPanel(answers)
      await opened.user.click(screen.getByRole("button", { name: "Secrets and login" }))
      await screen.findByRole("heading", { name: "Secrets and login" })
      await act(() => opened.clock.advance(0))
      return opened
    }

    const secret = (name: string) =>
      within(within(screen.getByRole("list", { name: "Secrets" })).getByRole("listitem", { name }))

    it("lists each secret, the variable it lands in, and whether it is set, with Set or Replace", async () => {
      await openSecrets({})

      expect(secret("API key").getByText("Set")).toBeDefined()
      expect(secret("API key").getByText("ANTHROPIC_API_KEY")).toBeDefined()
      expect(secret("API key").queryByText("api_key")).toBeNull()
      expect(secret("GitHub token").getByText("GH_TOKEN")).toBeDefined()
      expect(secret("API key").getByRole("button", { name: "Replace" })).toBeDefined()
      expect(secret("GitHub token").getByText("Not set")).toBeDefined()
      expect(secret("GitHub token").getByRole("button", { name: "Set" })).toBeDefined()
    })

    it("names no variable for the API key while the instance's model cannot be read", async () => {
      const unread = setup.secrets.map((each) =>
        each.name === "api_key" ? { ...each, variable: null } : each,
      )
      await openSecrets({
        "instance.status": () => status({ setup: { ...setup, secrets: unread } }),
      })

      expect(secret("API key").queryByText("api_key")).toBeNull()
      expect(secret("API key").queryByRole("code")).toBeNull()
    })

    it("replaces a secret, and the notice asks for a recreate", async () => {
      let replaced = false
      const { user, clock, caller } = await openSecrets({
        "instance.status": () => status({ recreate_reasons: replaced ? ["secrets"] : [] }),
        "instance.secrets.set": () => {
          replaced = true
          return { operation_id: "op-secret", instance_id: "instance-1" }
        },
        "operation.get": () => operation({ kind: "secrets", state: "succeeded" }),
      })

      expect(screen.queryByRole("region", { name: "Recreate to apply" })).toBeNull()
      await user.click(secret("API key").getByRole("button", { name: "Replace" }))
      await user.type(secret("API key").getByLabelText("New value for API key"), "sk-new")
      await user.click(secret("API key").getByRole("button", { name: "Save" }))
      await act(() => clock.advance(0))

      expect(caller.calls.find((call) => call.method === "instance.secrets.set")?.params).toEqual({
        instance_id: "instance-1",
        secrets: { api_key: "sk-new" },
      })
      const notice = within(screen.getByRole("region", { name: "Recreate to apply" }))
      expect(notice.getByText("Replaced secrets")).toBeDefined()
      expect(within(section("Secrets and login")).getByText("Recreate to apply")).toBeDefined()
    })

    it("signs an expired login in again and shows its URL and code", async () => {
      const { user, clock, caller } = await openSecrets({
        "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
        "operation.get": () =>
          operation({
            kind: "login",
            steps: [
              {
                name: "sign-in",
                state: "running",
                detail: "Waiting for you to sign in to Claude Code.",
                prompt: { url: "https://claude.ai/device", code: "WXYZ-98765" },
              },
            ],
          }),
      })

      const login = within(screen.getByRole("listitem", { name: "Claude Code" }))
      await user.click(login.getByRole("button", { name: "Sign in again" }))
      await act(() => clock.advance(0))

      expect(caller.calls.find((call) => call.method === "instance.login.start")?.params).toEqual({
        instance_id: "instance-1",
        login_id: "claude",
      })
      expect(login.getByText("WXYZ-98765")).toBeDefined()
      expect(login.getByRole("link", { name: "https://claude.ai/device" })).toBeDefined()
    })

    const expired = "The code expired. Sign in again for a new one."

    it("keeps a signed-in login signed in when signing it in again expires, and says so", async () => {
      const { user, clock, caller } = await openSecrets({
        "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "login", state: "failed", detail: expired }),
      })

      const login = within(screen.getByRole("listitem", { name: "Claude Code" }))
      await user.click(login.getByRole("button", { name: "Sign in again" }))
      await act(() => clock.advance(0))

      expect(caller.calls.filter((call) => call.method === "instance.status")).toHaveLength(2)
      expect(login.getByText("Signed in").getAttribute("data-variant")).toBe("success")
      expect(login.queryByText("Failed")).toBeNull()
      expect(
        login.getByText(
          `The new sign-in did not finish, and the one before it still works. ${expired}`,
        ),
      ).toBeDefined()
      expect(login.getByRole("button", { name: "Sign in again" })).toBeDefined()
    })

    it("says a sign-in failed on a login whose last sign-in failed", async () => {
      const failed = { ...setup, logins: [{ ...setup.logins[0]!, state: "failed" as const }] }
      const { user, clock } = await openSecrets({
        "instance.status": () => status({ setup: failed }),
        "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
        "operation.get": () => operation({ kind: "login", state: "failed", detail: expired }),
      })

      const login = within(screen.getByRole("listitem", { name: "Claude Code" }))
      await user.click(login.getByRole("button", { name: "Sign in again" }))
      await act(() => clock.advance(0))

      expect(login.getByText("Failed")).toBeDefined()
      expect(login.getByText(expired)).toBeDefined()
      expect(login.getByRole("button", { name: "Sign in again" })).toBeDefined()
    })
  })

  describe("Recreate to apply", () => {
    it("lists every reason, and says so beside each section that has one", async () => {
      const { clock } = openPanel({
        "instance.status": () => status({ recreate_reasons: ["secrets"] }),
      })
      await act(() => clock.advance(0))

      const notice = within(screen.getByRole("region", { name: "Recreate to apply" }))
      expect(notice.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
        "Replaced secrets",
      ])
      expect(notice.getAllByRole("button").map((button) => button.textContent)).toEqual([
        "Recreate",
      ])
      expect(within(section("Secrets and login")).getByText("Recreate to apply")).toBeDefined()
      expect(within(section("Permissions")).queryByText("Recreate to apply")).toBeNull()
    })

    it("recreates with one button, shows its steps, and goes once nothing waits", async () => {
      let recreated = false
      const polls = [
        operation({
          steps: [
            { name: "validate", state: "succeeded", detail: "Revalidating the configuration." },
            { name: "drain", state: "running", detail: "Draining accepted work." },
          ],
        }),
        operation({ state: "succeeded", detail: "Container recreated." }),
      ]
      const { user, clock, caller } = openPanel({
        "instance.status": () => status({ recreate_reasons: recreated ? [] : ["secrets"] }),
        "instance.recreate": () => {
          recreated = true
          return { operation_id: "op-1", instance_id: "instance-1" }
        },
        "operation.get": () => (polls.length > 1 ? polls.shift() : polls[0]) as OperationGetResult,
      })
      await act(() => clock.advance(0))

      const notice = within(screen.getByRole("region", { name: "Recreate to apply" }))
      await user.click(notice.getByRole("button", { name: "Recreate" }))
      await act(() => clock.advance(0))

      const steps = within(notice.getByRole("list", { name: "Recreate steps" }))
      expect(steps.getByRole("listitem", { name: "drain" })).toBeDefined()
      expect(notice.getByRole("button", { name: /Recreate/ })).toHaveProperty("disabled", true)
      await act(() => clock.advance(1_000))

      expect(caller.calls.find((call) => call.method === "instance.recreate")?.params).toEqual({
        instance_id: "instance-1",
      })
      expect(screen.queryByRole("region", { name: "Recreate to apply" })).toBeNull()
      expect(within(section("Secrets and login")).queryByText("Recreate to apply")).toBeNull()
    })

    it("says why a recreate failed, and offers it again", async () => {
      const { user, clock } = openPanel({
        "instance.status": () => status({ recreate_reasons: ["secrets"] }),
        "instance.recreate": () => ({ operation_id: "op-1", instance_id: "instance-1" }),
        "operation.get": () =>
          operation({ state: "failed", detail: "The image is gone.", steps: [] }),
      })
      await act(() => clock.advance(0))

      const notice = within(screen.getByRole("region", { name: "Recreate to apply" }))
      await user.click(notice.getByRole("button", { name: "Recreate" }))
      await act(() => clock.advance(0))

      expect(notice.getByText("The image is gone.")).toBeDefined()
      expect(notice.getByRole("button", { name: "Recreate" })).toHaveProperty("disabled", false)
    })
  })
})
