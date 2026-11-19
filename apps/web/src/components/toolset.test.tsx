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
    },
    { group: "browser", on: false, default_state: "loaded", loaded_tokens: 0, tools: [] },
    { group: "authored", on: true, default_state: "loaded", loaded_tokens: 0, tools: null },
  ],
  mcp_server_groups: [
    { server: "fetch", on: true, default_state: "searchable", loaded_tokens: 0, tools: null },
  ],
  search: { backend: "brave", variable: "BRAVE_API_KEY", is_set: false },
  mcp_servers: [{ name: "fetch", trust_hints: true, state: "up-to-date" }],
  packages: ["httpx>=0.27"],
  authored_tools: [
    { name: "lookup", read_only: true },
    { name: "post", read_only: false },
  ],
  browser: true,
}

/** Ada's toolset on a hub that reads `toolset`, and, while she runs, her own contract. */
function renderToolset({
  running = true,
  failed = [],
  signIn = null,
}: { running?: boolean; failed?: string[]; signIn?: LoginState | null } = {}) {
  let marked = new Set(["lookup"])
  const hub = stubCaller({
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
