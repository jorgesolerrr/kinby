import type { InstanceToolsetResult } from "@kinby/contract"
import { fakeClock, stubCaller } from "@kinby/contract/testing"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { Toolset } from "@/components/toolset"

const toolset: InstanceToolsetResult = {
  default_tools: [
    { name: "read", on: true },
    { name: "bash", on: false },
    { name: "web_search", on: true },
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
function renderToolset({ running = true }: { running?: boolean } = {}) {
  let marked = new Set(["lookup"])
  const hub = stubCaller({
    "instance.toolset.get": () => ({
      ...toolset,
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
    />,
  )
  return { hub, instance }
}

const item = async (list: string, name: string) =>
  within(within(await screen.findByRole("list", { name: list })).getByRole("listitem", { name }))

describe("the instance's toolset", () => {
  it("shows each default tool on or off, the search key, and the browser", async () => {
    renderToolset()

    expect((await item("Toolset", "Default tools")).getByText("On: read, web_search")).toBeDefined()
    expect((await item("Toolset", "Default tools")).getByText("Off: bash")).toBeDefined()
    expect(
      (await item("Toolset", "Web search")).getByText("Brave. Its key, BRAVE_API_KEY, is not set."),
    ).toBeDefined()
    expect((await item("Toolset", "Packages")).getByText("httpx>=0.27")).toBeDefined()
    expect((await item("Toolset", "Browser")).getByText("On")).toBeDefined()
  })

  it("shows each picked MCP server with whether its read-only hints are trusted", async () => {
    renderToolset()

    expect((await item("MCP servers", "fetch")).getByText("Read-only hints trusted.")).toBeDefined()
  })

  it("shows each authored tool with its read-only mark", async () => {
    renderToolset()

    const lookup = (await item("Authored tools", "lookup")).getByRole("switch", {
      name: "Read-only",
    })
    const post = (await item("Authored tools", "post")).getByRole("switch", { name: "Read-only" })
    expect([lookup.ariaChecked, post.ariaChecked]).toEqual(["true", "false"])
  })

  it("marks an authored tool read-only through the instance, and reads the toolset again", async () => {
    const { hub, instance } = renderToolset()
    const user = userEvent.setup()

    const post = (await item("Authored tools", "post")).getByRole("switch", { name: "Read-only" })
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

    const post = (await item("Authored tools", "post")).getByRole("switch", { name: "Read-only" })
    await user.click(post)

    expect(post.hasAttribute("data-disabled")).toBe(true)
    expect(instance.calls).toEqual([])
  })
})
