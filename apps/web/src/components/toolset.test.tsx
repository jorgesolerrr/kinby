import type { InstanceToolsetResult, LoginState } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { Toolset } from "@/components/toolset"

const toolset: InstanceToolsetResult = {
  built_in_groups: [
    {
      group: "defaults",
      on: true,
      default_state: "loaded",
      loaded_tokens: 1234,
      tools: [
        {
          name: "bash",
          description: "Run a shell command.",
          state: "off",
          source: "instance",
          tokens: 300,
        },
        {
          name: "read",
          description: "Read a file.",
          state: "loaded",
          source: "group",
          tokens: 1234,
        },
      ],
      instance_states: { on: true, default_state: null, tool_states: { bash: "off" } },
    },
    {
      group: "memory",
      on: true,
      default_state: "searchable",
      loaded_tokens: 0,
      tools: [
        {
          name: "memory_search",
          description: "Search facts.",
          state: "searchable",
          source: "group",
          tokens: 90,
        },
      ],
      instance_states: { on: true, default_state: "searchable", tool_states: {} },
    },
    {
      group: "browser",
      on: false,
      default_state: "loaded",
      loaded_tokens: 0,
      tools: [],
      instance_states: { on: false, default_state: null, tool_states: {} },
    },
    {
      group: "authored",
      on: true,
      default_state: "loaded",
      loaded_tokens: 0,
      tools: null,
      instance_states: { on: true, default_state: null, tool_states: {} },
    },
  ],
  mcp_server_groups: [
    {
      server: "fetch",
      on: true,
      default_state: "searchable",
      loaded_tokens: 0,
      tools: null,
      instance_states: { on: true, default_state: null, tool_states: {} },
    },
    {
      server: "linear",
      on: true,
      default_state: "off",
      loaded_tokens: 50,
      tools: [
        {
          name: "list_issues",
          description: "List issues.",
          state: "loaded",
          source: "instance",
          tokens: 50,
        },
      ],
      instance_states: {
        on: true,
        default_state: "off",
        tool_states: { list_issues: "loaded" },
      },
    },
  ],
  search: { backend: "brave", variable: "BRAVE_API_KEY", is_set: false },
  mcp_servers: [{ name: "fetch", trust_hints: true, state: "up-to-date" }],
  packages: ["httpx>=0.27"],
  authored_tools: [
    { name: "lookup", read_only: true },
    { name: "post", read_only: false },
  ],
  browser: true,
  hash: "read-hash",
}

/** Ada's toolset on a hub that reads `toolset`, and, while she runs, her own contract. */
function renderToolset({
  running = true,
  failed = [],
  signIn = null,
}: { running?: boolean; failed?: string[]; signIn?: LoginState | null } = {}) {
  let marked = new Set(["lookup"])
  const hub = stubCaller({
    "instance.tool_states.set": () => toolset,
    "instance.toolset.get": () => ({
      ...toolset,
      mcp_servers: toolset.mcp_servers.map((server) => ({ ...server, sign_in: signIn })),
      authored_tools: toolset.authored_tools.map(({ name }) => ({
        name,
        read_only: marked.has(name),
      })),
    }),
  })
  const instance = stubCaller({
    "tool.read_only.set": ({ name, read_only }) => {
      marked = new Set(read_only ? [...marked, name] : [...marked].filter((each) => each !== name))
      return { tools: [], warnings: [] }
    },
  })
  render(
    <Toolset
      caller={hub}
      client={running ? instance : undefined}
      clock={fakeClock()}
      instanceId="ada"
      failed={failed}
    />,
  )
  return { hub, instance }
}

/** What each tool states set sent the hub, in order. */
const states = (hub: ReturnType<typeof renderToolset>["hub"]) =>
  hub.calls.filter((call) => call.method === "instance.tool_states.set").map((call) => call.params)

const item = async (list: string, name: string) =>
  within(within(await screen.findByRole("list", { name: list })).getByRole("listitem", { name }))

describe("the instance's toolset", () => {
  it("shows each tool group with its default state and what its loaded tools add", async () => {
    renderToolset()

    expect(
      (await item("Tool groups", "Default tools")).getByText(
        "Loaded by default. Its loaded tools add 1,234 tokens to every call.",
      ),
    ).toBeDefined()
    expect(
      (await item("Tool groups", "Memory tools")).getByText(
        "Searchable by default. Its loaded tools add 0 tokens to every call.",
      ),
    ).toBeDefined()
    expect((await item("Tool groups", "Browser tools")).getByText("Off.")).toBeDefined()
  })

  it("shows each tool of a group with its state and description", async () => {
    renderToolset()

    const bash = await item("Default tools", "bash")
    expect(bash.getByText("Off")).toBeDefined()
    expect(bash.getByText("Run a shell command.")).toBeDefined()
    expect((await item("Memory tools", "memory_search")).getByText("Searchable")).toBeDefined()
  })

  it("says an MCP server's tools are listed after the first turn connected to it", async () => {
    renderToolset()

    expect(
      (await item("Tool groups", "fetch")).getByText(
        "Its tools are listed after the first turn connected to it.",
      ),
    ).toBeDefined()
    expect(
      (await item("Tool groups", "Authored tools")).getByText(
        "Its tools are listed after the instance's next turn.",
      ),
    ).toBeDefined()
  })

  it("switches a group off through the hub, and reads the toolset again", async () => {
    const { hub } = renderToolset()
    const user = userEvent.setup()

    await user.click(
      (await item("Tool groups", "Memory tools")).getByRole("switch", { name: "On" }),
    )
    await user.click((await item("Tool groups", "linear")).getByRole("switch", { name: "On" }))

    expect(states(hub)).toEqual([
      {
        instance_id: "ada",
        built_in_groups: {
          memory: { on: false, default_state: "searchable", tool_states: {} },
        },
        hash: "read-hash",
      },
      {
        instance_id: "ada",
        mcp_servers: {
          linear: { on: false, default_state: "off", tool_states: { list_issues: "loaded" } },
        },
        hash: "read-hash",
      },
    ])
    await waitFor(() =>
      expect(hub.calls.filter((call) => call.method === "instance.toolset.get")).toHaveLength(3),
    )
  })

  it("gives a tool its own state, and the group a default state", async () => {
    const { hub } = renderToolset()
    const user = userEvent.setup()

    const read = await item("Default tools", "read")
    await user.click(read.getByRole("button", { name: "Searchable" }))
    const memory = await item("Tool groups", "Memory tools")
    await user.click(
      within(memory.getByRole("group", { name: "Default state" })).getByRole("button", {
        name: "Loaded",
      }),
    )

    expect(states(hub)).toEqual([
      {
        instance_id: "ada",
        built_in_groups: {
          defaults: {
            on: true,
            default_state: null,
            tool_states: { bash: "off", read: "searchable" },
          },
        },
        hash: "read-hash",
      },
      {
        instance_id: "ada",
        built_in_groups: { memory: { on: true, default_state: "loaded", tool_states: {} } },
        hash: "read-hash",
      },
    ])
  })

  it("marks a state the instance changed, and resets it to the group's", async () => {
    const { hub } = renderToolset()
    const user = userEvent.setup()

    const bash = await item("Default tools", "bash")
    expect(bash.getByText("Changed")).toBeDefined()
    expect((await item("Default tools", "read")).queryByText("Changed")).toBeNull()
    await user.click(bash.getByRole("button", { name: "Reset" }))

    expect(states(hub)).toEqual([
      {
        instance_id: "ada",
        built_in_groups: { defaults: { on: true, default_state: null, tool_states: {} } },
        hash: "read-hash",
      },
    ])
  })

  it("resets a built-in group's default state to kinby's, and a server's to the library's", async () => {
    const { hub } = renderToolset()
    const user = userEvent.setup()

    const memory = await item("Tool groups", "Memory tools")
    await user.click(memory.getByRole("button", { name: "Reset to kinby's default" }))
    const linear = await item("Tool groups", "linear")
    await user.click(linear.getByRole("button", { name: "Reset to the library's default" }))

    expect(states(hub)).toEqual([
      {
        instance_id: "ada",
        built_in_groups: { memory: { on: true, default_state: null, tool_states: {} } },
        hash: "read-hash",
      },
      {
        instance_id: "ada",
        mcp_servers: {
          linear: { on: true, default_state: null, tool_states: { list_issues: "loaded" } },
        },
        hash: "read-hash",
      },
    ])
    expect(
      (await item("Tool groups", "Default tools")).queryByRole("button", {
        name: "Reset to kinby's default",
      }),
    ).toBeNull()
  })

  it("keeps a switched-off group's tools fixed", async () => {
    renderToolset()

    const browser = await item("Tool groups", "Browser tools")
    expect(browser.queryByRole("group", { name: "Default state" })).toBeNull()
    expect(browser.getByRole("switch", { name: "On" }).ariaChecked).toBe("false")
  })

  it("shows the search key, the packages and the browser", async () => {
    renderToolset()

    expect(
      (await item("Toolset", "Web search")).getByText("Brave. Its key, BRAVE_API_KEY, is not set."),
    ).toBeDefined()
    expect((await item("Toolset", "Packages")).getByText("httpx>=0.27")).toBeDefined()
    expect((await item("Toolset", "Browser")).getByText("On")).toBeDefined()
  })

  it("shows each picked MCP server with whether its read-only hints are trusted", async () => {
    renderToolset()

    expect((await item("Toolset", "fetch")).getByText("Read-only hints trusted.")).toBeDefined()
  })

  it("shows a picked MCP server ready, or failed when it failed at the last turn", async () => {
    renderToolset()
    expect((await item("Toolset", "fetch")).getByText("Ready")).toBeDefined()
    cleanup()

    renderToolset({ failed: ["fetch"] })
    expect((await item("Toolset", "fetch")).getByText("Failed")).toBeDefined()
  })

  it("shows a picked MCP server not signed in while its sign-in is pending", async () => {
    renderToolset({ signIn: "pending", failed: ["fetch"] })
    expect((await item("Toolset", "fetch")).getByText("Not signed in")).toBeDefined()
    cleanup()

    renderToolset({ signIn: "signed_in" })
    expect((await item("Toolset", "fetch")).getByText("Ready")).toBeDefined()
  })

  it("shows each authored tool with its read-only mark", async () => {
    renderToolset()

    const lookup = (await item("Toolset", "lookup")).getByRole("switch", {
      name: "Read-only",
    })
    const post = (await item("Toolset", "post")).getByRole("switch", { name: "Read-only" })
    expect([lookup.ariaChecked, post.ariaChecked]).toEqual(["true", "false"])
  })

  it("marks an authored tool read-only through the instance, and reads the toolset again", async () => {
    const { hub, instance } = renderToolset()
    const user = userEvent.setup()

    const post = (await item("Toolset", "post")).getByRole("switch", { name: "Read-only" })
    await user.click(post)

    expect(instance.calls).toEqual([
      { method: "tool.read_only.set", params: { name: "post", read_only: true } },
    ])
    await waitFor(() => expect(post.ariaChecked).toBe("true"))
    expect(hub.calls.filter((call) => call.method === "instance.toolset.get")).toHaveLength(2)
  })

  it("keeps the read-only marks fixed while the instance is stopped", async () => {
    const { instance } = renderToolset({ running: false })
    const user = userEvent.setup()

    const post = (await item("Toolset", "post")).getByRole("switch", { name: "Read-only" })
    await user.click(post)

    expect(post.hasAttribute("data-disabled")).toBe(true)
    expect(instance.calls).toEqual([])
  })
})
