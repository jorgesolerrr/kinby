import { CallError } from "@kinby/contract"
import type {
  ConfigChange,
  InstanceSetup,
  InstanceStatusResult,
  InstanceSummary,
  OperationGetResult,
  PackageConfigResult,
  PackageConfigSetCommand,
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
    expect(labels("Instance")).toEqual([
      "Secrets and login",
      "Package config",
      "Package and version",
    ])
    expect(screen.getByRole("button", { name: "Behavior prompt" })).toHaveProperty(
      "disabled",
      false,
    )
    expect(screen.getByRole("button", { name: "Recap prompt" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Skills" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Tools" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Routines" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Permissions" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Package config" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Manifest" })).toHaveProperty("disabled", false)
    expect(screen.getByRole("button", { name: "Package and version" })).toHaveProperty(
      "disabled",
      false,
    )
    expect(await screen.findByRole("heading", { name: "Behavior prompt" })).toBeDefined()
  })

  it("shows no list marker beside a section", async () => {
    openPanel({})

    const sections = screen.getByRole("navigation", { name: "Config sections" })
    expect(within(sections).getAllByRole("listitem").filter(marked)).toEqual([])
    expect(await screen.findByRole("heading", { name: "Behavior prompt" })).toBeDefined()
  })

  it("says the package section is behind the hub in place of its hint", async () => {
    const section = () => screen.getByRole("button", { name: "Package and version" })

    openPanel({}, ada)
    expect(section().textContent).toContain("template, installed, update")
    cleanup()
    const { user } = openPanel({}, behind)

    expect(section().textContent).toContain("behind the hub")
    expect(section().textContent).not.toContain("template, installed, update")
    await user.click(section())
    expect(await screen.findByRole("heading", { name: "Package and version" })).toBeDefined()
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
    await user.click(within(alert).getByRole("button", { name: "Open Package and version" }))

    expect(await screen.findByRole("heading", { name: "Package and version" })).toBeDefined()
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
            tier: "package",
            description: "Plan work.",
            source: "kinby-factory 0.3.0",
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
    expect(await screen.findByRole("button", { name: "planning, package skill" })).toBeDefined()
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

  it("opens on Package and version when a link names it, and drops it from the URL on leaving", async () => {
    window.history.replaceState(null, "", "/instances/instance-1/config/package")
    try {
      const { user } = openPanel({}, behind)

      expect(await screen.findByRole("heading", { name: "Package and version" })).toBeDefined()
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

  describe("Package config", () => {
    // The software factory's shape: its check commands and its skills by name, with one field of
    // each other type the form offers. An enum from a StrEnum arrives as a reference.
    const factory: PackageConfigResult = {
      schema: {
        $defs: { Client: { enum: ["claude", "codex"], title: "Client", type: "string" } },
        additionalProperties: false,
        properties: {
          check_commands: {
            description: "Commands that must pass before a pull request opens.",
            items: { type: "string" },
            title: "Check Commands",
            type: "array",
          },
          skills: { additionalProperties: { type: "string" }, title: "Skills", type: "object" },
          client: { $ref: "#/$defs/Client" },
          round_limit: { title: "Round Limit", type: "integer" },
          review: { default: false, title: "Review", type: "boolean" },
          github_token: { title: "Github Token", type: "string" },
        },
        required: ["check_commands", "skills", "client", "round_limit", "github_token"],
        title: "FactoryConfig",
        type: "object",
      },
      values: {
        check_commands: ["uv run ruff check .", "uv run pytest"],
        skills: { implement: "implement-ticket", review: "adversarial-review" },
        client: "claude",
        round_limit: 2,
        github_token: "GH_TOKEN",
      },
      hash: "yaml-1",
    }

    async function openPackageConfig(answers: Answers) {
      const opened = openPanel({ "package.config.get": () => factory, ...answers })
      await opened.user.click(screen.getByRole("button", { name: "Package config" }))
      await screen.findByRole("heading", { name: "Package config" })
      return opened
    }

    const sent = (caller: ReturnType<typeof openPanel>["caller"]) =>
      caller.calls
        .filter((call) => call.method === "package.config.set")
        .map((call) => call.params as PackageConfigSetCommand)

    it("renders the form the package declares and saves its values", async () => {
      const { caller, user } = await openPackageConfig({
        "package.config.set": ({ values }) => ({ ...factory, values, hash: "yaml-2" }),
      })

      const commands = await screen.findByRole("textbox", { name: "Check Commands" })
      expect(commands).toHaveProperty("value", "uv run ruff check .\nuv run pytest")
      expect(screen.getByText("Commands that must pass before a pull request opens.")).toBeDefined()
      expect(screen.getByRole("textbox", { name: "Skills 1 name" })).toHaveProperty(
        "value",
        "implement",
      )
      expect(screen.getByRole("textbox", { name: "Skills 1 value" })).toHaveProperty(
        "value",
        "implement-ticket",
      )
      expect(screen.getByRole("combobox", { name: "Client" }).textContent).toContain("claude")
      expect(screen.getByRole("spinbutton", { name: "Round Limit" })).toHaveProperty("value", "2")
      expect(screen.getByRole("switch", { name: "Review" }).getAttribute("aria-checked")).toBe(
        "false",
      )
      expect(screen.getByText(/rewrites package\.yaml from these values/)).toBeDefined()

      await user.type(commands, "\nuv run ty check")
      const reviewSkill = screen.getByRole("textbox", { name: "Skills 2 value" })
      await user.clear(reviewSkill)
      await user.type(reviewSkill, "my-review")
      await user.click(screen.getByRole("button", { name: "Remove implement" }))
      await user.click(screen.getByRole("button", { name: "Add to Skills" }))
      await user.type(screen.getByRole("textbox", { name: "Skills 2 name" }), "pull_request")
      await user.type(screen.getByRole("textbox", { name: "Skills 2 value" }), "open-pr")
      await user.click(screen.getByRole("combobox", { name: "Client" }))
      await user.click(await screen.findByRole("option", { name: "codex" }))
      await user.clear(screen.getByRole("spinbutton", { name: "Round Limit" }))
      await user.type(screen.getByRole("spinbutton", { name: "Round Limit" }), "3")
      await user.click(screen.getByRole("switch", { name: "Review" }))
      await user.click(screen.getByRole("button", { name: "Save" }))

      expect(
        await screen.findByText("Saved. It applies once the instance is recreated."),
      ).toBeDefined()
      expect(sent(caller)).toEqual([
        {
          values: {
            check_commands: ["uv run ruff check .", "uv run pytest", "uv run ty check"],
            skills: { review: "my-review", pull_request: "open-pr" },
            client: "codex",
            round_limit: 3,
            review: true,
            github_token: "GH_TOKEN",
          },
          hash: "yaml-1",
        },
      ])
    })

    it("adds a saved package config to the notice", async () => {
      let saved = false
      const { user, clock } = await openPackageConfig({
        "instance.status": () => status({ recreate_reasons: saved ? ["package_config"] : [] }),
        "package.config.set": ({ values }) => {
          saved = true
          return { ...factory, values, hash: "yaml-2" }
        },
      })
      await act(() => clock.advance(0))

      expect(screen.queryByRole("region", { name: "Recreate to apply" })).toBeNull()
      await user.click(screen.getByRole("switch", { name: "Review" }))
      await user.click(screen.getByRole("button", { name: "Save" }))
      await screen.findByText("Saved. It applies once the instance is recreated.")

      const notice = within(await screen.findByRole("region", { name: "Recreate to apply" }))
      expect(notice.getByText("The edited package config")).toBeDefined()
    })

    it("shows the package validator's errors next to their fields", async () => {
      const { user } = await openPackageConfig({
        "package.config.set": () => {
          throw new CallError({
            code: "INVALID_ARGUMENT",
            message: "Some values are invalid.",
            retryable: false,
            fields: {
              "check_commands.1": "a check command cannot be empty",
              github_token: '"GITHUB_TOKEN" is not a secret field this package declares.',
            },
          })
        },
      })

      const token = await screen.findByRole("textbox", { name: "Github Token" })
      await user.clear(token)
      await user.type(token, "GITHUB_TOKEN")
      await user.click(screen.getByRole("button", { name: "Save" }))

      const tokenError = await screen.findByText(
        '"GITHUB_TOKEN" is not a secret field this package declares.',
      )
      expect(token.closest("[data-slot=field]")?.contains(tokenError)).toBe(true)
      const commandError = screen.getByText("Item 2: a check command cannot be empty")
      expect(
        screen
          .getByRole("textbox", { name: "Check Commands" })
          .closest("[data-slot=field]")
          ?.contains(commandError),
      ).toBe(true)
    })

    it("offers to load theirs when package.yaml changed since it was opened", async () => {
      let theirs = false
      const { caller, user } = await openPackageConfig({
        "package.config.get": () =>
          theirs
            ? { ...factory, values: { ...factory.values, round_limit: 5 }, hash: "yaml-3" }
            : factory,
        "package.config.set": () => {
          theirs = true
          throw new CallError({ code: "STALE", message: "changed", retryable: false })
        },
      })

      await user.click(await screen.findByRole("switch", { name: "Review" }))
      await user.click(screen.getByRole("button", { name: "Save" }))
      const alert = await screen.findByRole("alert")
      await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

      await waitFor(() =>
        expect(screen.getByRole("spinbutton", { name: "Round Limit" })).toHaveProperty(
          "value",
          "5",
        ),
      )
      await user.click(screen.getByRole("switch", { name: "Review" }))
      await user.click(screen.getByRole("button", { name: "Save" }))
      expect(sent(caller).at(-1)?.hash).toBe("yaml-3")
    })

    it("says a vanilla instance has no package config", async () => {
      await openPackageConfig({
        "package.config.get": () => {
          throw new CallError({
            code: "NOT_FOUND",
            message: "The instance runs no package, so it has no config.",
            retryable: false,
          })
        },
      })

      expect(await screen.findByText("No package config")).toBeDefined()
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull()
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
        "instance.status": () => status({ recreate_reasons: ["secrets", "package_config"] }),
      })
      await act(() => clock.advance(0))

      const notice = within(screen.getByRole("region", { name: "Recreate to apply" }))
      expect(notice.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
        "Replaced secrets",
        "The edited package config",
      ])
      expect(notice.getAllByRole("button").map((button) => button.textContent)).toEqual([
        "Recreate",
      ])
      expect(within(section("Secrets and login")).getByText("Recreate to apply")).toBeDefined()
      expect(within(section("Package config")).getByText("Recreate to apply")).toBeDefined()
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
        "instance.status": () => status({ recreate_reasons: ["package_config"] }),
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
