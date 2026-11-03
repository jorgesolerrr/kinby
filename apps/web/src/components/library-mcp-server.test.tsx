import { CallError } from "@kinby/contract"
import type { LibraryEntry, McpServer } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { screen, waitFor, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { listed, names, openLibrary, refused } from "@/components/library-testing"

const fetchServer: McpServer = {
  transport: { type: "uvx", package: "mcp-server-fetch", args: ["--ignore-robots"] },
  env: ["FETCH_USER_AGENT"],
  include: ["fetch"],
}

/**
 * A page over a library holding the MCP server fetch, defined as `server`, which writes and
 * deletes change. Each read of it returns the next hash.
 */
function openServersPage({
  write,
  server = fetchServer,
}: { write?: () => void; server?: McpServer } = {}) {
  let reads = 0
  let entries: LibraryEntry[] = [
    ...listed.entries,
    { kind: "mcp_server", name: "fetch", description: null, source: "written" },
  ]
  const caller = stubCaller({
    "library.list": () => ({ entries }),
    "library.mcp_server.read": ({ name }) => ({ name, server, hash: `h-${++reads}` }),
    "library.mcp_server.write": ({ name, server }) => {
      write?.()
      if (!entries.some((entry) => entry.name === name)) {
        entries = [...entries, { kind: "mcp_server", name, description: null, source: "written" }]
      }
      return { name, server, hash: "w-1" }
    },
    "library.mcp_server.delete": ({ name }) => {
      entries = entries.filter((entry) => entry.name !== name)
      return { entries }
    },
  })
  return { caller, user: openLibrary(caller, "/library/mcp-servers") }
}

const serverWrites = (caller: ReturnType<typeof openServersPage>["caller"]) =>
  caller.calls.filter((call) => /^library\.mcp_server\.(write|delete)$/.test(call.method))

describe("LibraryPage, MCP servers", () => {
  it("lists the MCP servers apart from the skills and the factories", async () => {
    openServersPage()

    const servers = await screen.findByRole("list", { name: "MCP servers" })
    expect(names(servers)).toEqual(["fetch, written MCP server"])
    expect(screen.getByRole("tab", { name: "MCP servers 1" })).toBeTruthy()
  })

  it("adds a remote server from its URL, the variables of its headers and its tools", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "github")
    await user.type(screen.getByRole("textbox", { name: "URL" }), "https://example.com/mcp")
    await user.type(
      screen.getByRole("textbox", { name: "Headers" }),
      "Authorization: GITHUB_AUTHORIZATION",
    )
    await user.type(screen.getByRole("textbox", { name: "Tools" }), "get_issue\nlist_issues")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("button", { name: "github, written MCP server" })).toBeTruthy()
    expect(serverWrites(caller)).toEqual([
      {
        method: "library.mcp_server.write",
        params: {
          name: "github",
          server: {
            transport: {
              type: "url",
              url: "https://example.com/mcp",
              headers: { Authorization: "GITHUB_AUTHORIZATION" },
            },
            env: [],
            include: ["get_issue", "list_issues"],
          },
          hash: null,
        },
      },
    ])
  })

  it("adds a local server from its uvx package, arguments and variables", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "git")
    await user.click(screen.getByRole("button", { name: "uvx" }))
    await user.type(screen.getByRole("textbox", { name: "Package" }), "mcp-server-git")
    await user.type(screen.getByRole("textbox", { name: "Arguments" }), "--repository\n/workspace")
    await user.type(screen.getByRole("textbox", { name: "Variables" }), "GIT_AUTHOR_NAME")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("button", { name: "git, written MCP server" })).toBeTruthy()
    expect(serverWrites(caller).map((call) => call.params)).toEqual([
      {
        name: "git",
        server: {
          transport: {
            type: "uvx",
            package: "mcp-server-git",
            args: ["--repository", "/workspace"],
          },
          env: ["GIT_AUTHOR_NAME"],
          include: [],
        },
        hash: null,
      },
    ])
  })

  it("shows why the hub refused a server", async () => {
    const { user } = openServersPage({
      write: () => {
        throw refused({ name: 'The library already holds an MCP server "fetch".' })
      },
    })

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "fetch")
    await user.type(screen.getByRole("textbox", { name: "URL" }), "https://example.com/mcp")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByText('The library already holds an MCP server "fetch".')).toBeTruthy()
  })

  it("opens a server's definition and saves an edit over the one it read", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "fetch, written MCP server" }))
    const package_ = await screen.findByRole("textbox", { name: "Package" })
    expect((package_ as HTMLInputElement).value).toBe("mcp-server-fetch")
    expect((screen.getByRole("textbox", { name: "Tools" }) as HTMLTextAreaElement).value).toBe(
      "fetch",
    )
    await user.type(screen.getByRole("textbox", { name: "Tools" }), "\nfetch_many")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText(/Saved\./)).toBeTruthy()
    expect(serverWrites(caller)).toEqual([
      {
        method: "library.mcp_server.write",
        params: {
          name: "fetch",
          server: { ...fetchServer, include: ["fetch", "fetch_many"] },
          hash: "h-1",
        },
      },
    ])
  })

  it("deletes a server after a confirm", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "fetch, written MCP server" }))
    await screen.findByRole("textbox", { name: "Package" })
    await user.click(screen.getByRole("button", { name: "Delete" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Delete fetch?" })
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "fetch, written MCP server" })).toBeNull(),
    )
    expect(serverWrites(caller)).toEqual([
      { method: "library.mcp_server.delete", params: { name: "fetch", hash: "h-1" } },
    ])
    expect(screen.queryByRole("textbox", { name: "Package" })).toBeNull()
  })

  it("reloads a server that changed since it was opened, and saves over the new version", async () => {
    let stale = true
    const { caller, user } = openServersPage({
      write: () => {
        if (!stale) return
        stale = false
        throw new CallError({ code: "STALE", message: "Changed.", retryable: false })
      },
    })

    await user.click(await screen.findByRole("button", { name: "fetch, written MCP server" }))
    await user.type(await screen.findByRole("textbox", { name: "Tools" }), "\nfetch_many")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("Changed since you opened it")).toBeTruthy()
    await user.click(screen.getByRole("button", { name: "Load again" }))

    await waitFor(() =>
      expect((screen.getByRole("textbox", { name: "Tools" }) as HTMLTextAreaElement).value).toBe(
        "fetch",
      ),
    )
    expect(screen.queryByText("Changed since you opened it")).toBeNull()
    await user.click(screen.getByRole("button", { name: "Save" }))
    await screen.findByText(/Saved\./)
    expect(serverWrites(caller).map((call) => call.params)).toEqual([
      { name: "fetch", server: { ...fetchServer, include: ["fetch", "fetch_many"] }, hash: "h-1" },
      { name: "fetch", server: fetchServer, hash: "h-2" },
    ])
  })

  it("keeps arguments the form cannot show one a line until they are edited", async () => {
    const server: McpServer = {
      transport: { type: "uvx", package: "odd", args: ["", " --flag ", "two\nlines"] },
      env: [],
      include: ["fetch"],
    }
    const { caller, user } = openServersPage({ server })

    await user.click(await screen.findByRole("button", { name: "fetch, written MCP server" }))
    await user.type(await screen.findByRole("textbox", { name: "Tools" }), "\nfetch_many")
    await user.click(screen.getByRole("button", { name: "Save" }))

    await screen.findByText(/Saved\./)
    expect(serverWrites(caller).map((call) => call.params)).toEqual([
      { name: "fetch", server: { ...server, include: ["fetch", "fetch_many"] }, hash: "h-1" },
    ])
  })
})
