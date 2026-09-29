import { CallError } from "@kinby/contract"
import type {
  ConfigChange,
  PackageConfigResult,
  PackageConfigSetCommand,
  PermissionsResult,
  PermissionsSetCommand,
  PromptResult,
} from "@kinby/contract"
import { type Answers, stubCaller } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
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

function openPanel(answers: Answers) {
  const caller = stubCaller({
    "prompt.get": ({ name }) =>
      name === "behavior"
        ? behavior
        : { content: "The shipped lens.", hash: "empty", default: true },
    "config.history": ({ file }) => ({ changes: file === "SYSTEM.md" ? [byTheAgent] : [] }),
    ...answers,
  })
  render(<ConfigPanel client={caller} />)
  return { caller, user: userEvent.setup() }
}

describe("ConfigPanel", () => {
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
      true,
    )
    expect(await screen.findByRole("heading", { name: "Behavior prompt" })).toBeDefined()
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
})
