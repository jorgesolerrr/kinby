import { CallError } from "@kinby/contract"
import type { McpRegistryServer } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { listed, openLibrary } from "@/components/library-testing"

const linear: McpRegistryServer = {
  name: "app.linear/linear",
  description: "Issues, projects and cycles.",
  version: "1.2.0",
  entry: { type: "http", url: "https://mcp.linear.app/mcp", headers: {} },
  reason: null,
  variables: [],
}

const acme: McpRegistryServer = {
  name: "io.github.acme/acme-mcp",
  description: "Acme's tickets.",
  version: "0.4.1",
  entry: {
    type: "stdio",
    command: "npx",
    args: ["@acme/mcp@0.4.1"],
    env: { ACME_API_KEY: "${ACME_API_KEY}", ACME_REGION: "${ACME_REGION:-eu}" },
  },
  reason: null,
  variables: [
    { name: "ACME_API_KEY", secret: true, description: "An API key from Acme's settings." },
    { name: "ACME_REGION", secret: false, description: null },
  ],
}

const fetchServer: McpRegistryServer = {
  name: "io.github.modelcontextprotocol/fetch",
  description: "Fetches web pages.",
  version: "2025.4.7",
  entry: { type: "stdio", command: "uvx", args: ["mcp-server-fetch@2025.4.7"], env: {} },
  reason: null,
  variables: [],
}

const dockerOnly: McpRegistryServer = {
  name: "io.github.boxed/boxed",
  description: "Runs in a container.",
  version: "3.0.0",
  entry: null,
  reason:
    "kinby runs a remote over streamable-http, or a package from npm or pypi over stdio. This record has a package from oci over stdio.",
  variables: [],
}

/** The MCP servers tab over a registry that finds `servers`, or fails with `failure`. */
function openRegistry({
  servers = [linear, acme, fetchServer, dockerOnly],
  failure,
}: { servers?: McpRegistryServer[]; failure?: CallError } = {}) {
  const caller = stubCaller({
    "library.list": () => listed,
    "mcp_registry.search": () => {
      if (failure !== undefined) throw failure
      return { servers }
    },
    "library.mcp_server.write": ({ name, server }) => ({ name, server, hash: "w-1" }),
  })
  return { caller, user: openLibrary(caller, "/library/mcp-servers") }
}

async function search(user: ReturnType<typeof openRegistry>["user"], words: string) {
  await user.click(await screen.findByRole("button", { name: "Search the MCP Registry" }))
  await user.type(screen.getByRole("searchbox", { name: "Search the MCP Registry" }), words)
  await user.keyboard("{Enter}")
}

const writes = (caller: ReturnType<typeof openRegistry>["caller"]) =>
  caller.calls.filter((call) => call.method === "library.mcp_server.write")

describe("LibraryPage, the MCP Registry", () => {
  it("lists each result with its description, its version and how it runs", async () => {
    const { caller, user } = openRegistry()

    await search(user, "tickets")

    const results = await screen.findByRole("list", { name: "MCP Registry results" })
    const linearRow = within(results).getByRole("listitem", { name: "app.linear/linear" })
    expect(within(linearRow).getByText("Issues, projects and cycles.")).toBeTruthy()
    expect(within(linearRow).getByText("1.2.0")).toBeTruthy()
    expect(within(linearRow).getByText("Remote")).toBeTruthy()
    const acmeRow = within(results).getByRole("listitem", { name: "io.github.acme/acme-mcp" })
    expect(within(acmeRow).getByText("npx")).toBeTruthy()
    const fetchRow = within(results).getByRole("listitem", {
      name: "io.github.modelcontextprotocol/fetch",
    })
    expect(within(fetchRow).getByText("uvx")).toBeTruthy()
    expect(caller.calls.filter((call) => call.method === "mcp_registry.search")).toEqual([
      { method: "mcp_registry.search", params: { words: "tickets" } },
    ])
  })

  it("shows why kinby can't run a result, and offers no way to add it", async () => {
    const { user } = openRegistry()

    await search(user, "boxed")

    const row = await screen.findByRole("listitem", { name: "io.github.boxed/boxed" })
    expect(within(row).getByText(dockerOnly.reason!)).toBeTruthy()
    expect(within(row).queryByRole("button")).toBeNull()
    const runnable = screen.getByRole("listitem", { name: "app.linear/linear" })
    expect(within(runnable).getByRole("button", { name: "Choose app.linear/linear" })).toBeTruthy()
  })

  it("opens a result in the form with the variables it needs, and adds it once saved", async () => {
    const { caller, user } = openRegistry()

    await search(user, "acme")
    await user.click(await screen.findByRole("button", { name: "Choose io.github.acme/acme-mcp" }))

    expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe(
      "acme-mcp",
    )
    expect(screen.getByRole("button", { name: "stdio" }).getAttribute("aria-pressed")).toBe("true")
    expect((screen.getByRole("textbox", { name: "Command" }) as HTMLInputElement).value).toBe("npx")
    expect((screen.getByRole("textbox", { name: "Env" }) as HTMLTextAreaElement).value).toBe(
      "ACME_API_KEY=${ACME_API_KEY}\nACME_REGION=${ACME_REGION:-eu}",
    )
    const needed = screen.getByRole("list", { name: "Variables it needs" })
    const key = within(needed).getByRole("listitem", { name: "ACME_API_KEY" })
    expect(within(key).getByText("Secret")).toBeTruthy()
    expect(within(key).getByText("An API key from Acme's settings.")).toBeTruthy()
    const region = within(needed).getByRole("listitem", { name: "ACME_REGION" })
    expect(within(region).queryByText("Secret")).toBeNull()
    expect(writes(caller)).toEqual([])

    await user.clear(screen.getByRole("textbox", { name: "Name" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "acme")
    await user.type(screen.getByRole("textbox", { name: "Loaded tools" }), "list_tickets")
    await user.click(screen.getByRole("button", { name: "Add" }))

    await screen.findByRole("list", { name: "MCP Registry results" })
    expect(writes(caller).map((call) => call.params)).toEqual([
      {
        name: "acme",
        server: {
          entry: acme.entry,
          default_state: "off",
          tool_states: { list_tickets: "loaded" },
        },
        hash: null,
      },
    ])
  })

  it("says the registry could not be reached, and leaves adding a server by hand", async () => {
    const { user } = openRegistry({
      failure: new CallError({
        code: "INTERNAL",
        message: "The MCP Registry at https://registry.modelcontextprotocol.io answered 503.",
        retryable: true,
      }),
    })

    await search(user, "acme")

    expect(await screen.findByText("The MCP Registry could not be reached")).toBeTruthy()
    expect(
      screen.getByText(
        "The MCP Registry at https://registry.modelcontextprotocol.io answered 503.",
      ),
    ).toBeTruthy()
    await user.click(screen.getByRole("button", { name: "Add by hand" }))

    expect(screen.getByRole("textbox", { name: "Paste an entry" })).toBeTruthy()
    expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("")
  })
})
