import { CallError } from "@kinby/contract"
import type { LibraryEntry, LibraryMcpServerWriteCommand, McpServer } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { screen, waitFor, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { listed, names, openLibrary, refused } from "@/components/library-testing"

const fetchServer: McpServer = {
  entry: {
    type: "stdio",
    command: "uvx",
    args: ["mcp-server-fetch", "--ignore-robots"],
    env: { FETCH_USER_AGENT: "${FETCH_USER_AGENT}" },
  },
  include: ["fetch"],
}

/**
 * A page over a library holding the MCP server fetch, defined as `server`, which writes and
 * deletes change. Each read of it returns the next hash. Writes wait for `held`.
 */
function openServersPage({
  write,
  server = fetchServer,
  held = Promise.resolve(),
}: { write?: () => void; server?: McpServer; held?: Promise<void> } = {}) {
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
  const writesHeld: typeof caller = {
    calls: caller.calls,
    call: async (method, params) => {
      if (method === "library.mcp_server.write") await held
      return caller.call(method, params)
    },
  }
  return { caller, user: openLibrary(writesHeld, "/library/mcp-servers") }
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

  it("adds a remote server from its URL, its headers and its tools", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "github")
    await user.type(screen.getByRole("textbox", { name: "URL" }), "https://example.com/mcp")
    await user.type(
      screen.getByRole("textbox", { name: "Headers" }),
      "Authorization: Bearer ${{GITHUB_TOKEN}\nX-Agent: kinby",
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
            entry: {
              type: "http",
              url: "https://example.com/mcp",
              headers: { Authorization: "Bearer ${GITHUB_TOKEN}", "X-Agent": "kinby" },
            },
            include: ["get_issue", "list_issues"],
          },
          hash: null,
        },
      },
    ])
  })

  it("adds a local server from its command, arguments and env", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "git")
    await user.click(screen.getByRole("button", { name: "stdio" }))
    expect((screen.getByRole("textbox", { name: "Command" }) as HTMLInputElement).value).toBe("uvx")
    await user.type(
      screen.getByRole("textbox", { name: "Arguments" }),
      "mcp-server-git\n--repository\n/workspace",
    )
    await user.type(
      screen.getByRole("textbox", { name: "Env" }),
      "GIT_AUTHOR_NAME=${{GIT_AUTHOR_NAME}\nGIT_EDITOR=true",
    )
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("button", { name: "git, written MCP server" })).toBeTruthy()
    expect(serverWrites(caller).map((call) => call.params)).toEqual([
      {
        name: "git",
        server: {
          entry: {
            type: "stdio",
            command: "uvx",
            args: ["mcp-server-git", "--repository", "/workspace"],
            env: { GIT_AUTHOR_NAME: "${GIT_AUTHOR_NAME}", GIT_EDITOR: "true" },
          },
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

  it("fills the form from a pasted entry, and adds it once reviewed", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "git")
    await user.click(screen.getByRole("textbox", { name: "Paste an entry" }))
    await user.paste(
      JSON.stringify({ command: "uvx", args: ["mcp-server-git"], env: { GIT_EDITOR: "true" } }),
    )

    expect(screen.getByRole("button", { name: "stdio" }).getAttribute("aria-pressed")).toBe("true")
    expect((screen.getByRole("textbox", { name: "Arguments" }) as HTMLTextAreaElement).value).toBe(
      "mcp-server-git",
    )
    expect((screen.getByRole("textbox", { name: "Env" }) as HTMLTextAreaElement).value).toBe(
      "GIT_EDITOR=true",
    )
    expect(
      (screen.getByRole("textbox", { name: "Paste an entry" }) as HTMLTextAreaElement).value,
    ).toBe("")
    expect(serverWrites(caller)).toEqual([])
    await user.type(screen.getByRole("textbox", { name: "Tools" }), "git_status")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("button", { name: "git, written MCP server" })).toBeTruthy()
    expect(serverWrites(caller).map((call) => call.params)).toEqual([
      {
        name: "git",
        server: {
          entry: {
            type: "stdio",
            command: "uvx",
            args: ["mcp-server-git"],
            env: { GIT_EDITOR: "true" },
          },
          include: ["git_status"],
        },
        hash: null,
      },
    ])
  })

  it("offers each server of a pasted block as its own entry, named by its key", async () => {
    const { caller, user } = openServersPage()
    const block = {
      mcpServers: {
        github: { type: "http", url: "https://api.githubcopilot.com/mcp/" },
        git: { command: "uvx", args: ["mcp-server-git"] },
      },
    }

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.click(screen.getByRole("textbox", { name: "Paste an entry" }))
    await user.paste(JSON.stringify(block))

    expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("github")
    expect((screen.getByRole("textbox", { name: "URL" }) as HTMLInputElement).value).toBe(
      "https://api.githubcopilot.com/mcp/",
    )
    expect(screen.getByText("Next from the paste: git.")).toBeTruthy()
    await user.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("git"),
    )
    expect((screen.getByRole("textbox", { name: "Arguments" }) as HTMLTextAreaElement).value).toBe(
      "mcp-server-git",
    )
    expect(screen.queryByText(/Next from the paste/)).toBeNull()
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("button", { name: "git, written MCP server" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "github, written MCP server" })).toBeTruthy()
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull()
    expect(
      serverWrites(caller).map((call) => (call.params as LibraryMcpServerWriteCommand).name),
    ).toEqual(["github", "git"])
  })

  it("takes no paste while a server is being added, so the write cannot drop it", async () => {
    let written = () => {}
    const { user } = openServersPage({ held: new Promise((resolve) => (written = resolve)) })
    const block = {
      github: { url: "https://api.githubcopilot.com/mcp/" },
      git: { command: "uvx", args: ["mcp-server-git"] },
    }

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.click(screen.getByRole("textbox", { name: "Paste an entry" }))
    await user.paste(JSON.stringify(block))
    await user.click(screen.getByRole("button", { name: "Add" }))
    const pasteBox = screen.getByRole("textbox", { name: "Paste an entry" }) as HTMLTextAreaElement
    expect(pasteBox.readOnly).toBe(true)
    await user.click(pasteBox)
    await user.paste(JSON.stringify({ fetch: { command: "uvx", args: ["mcp-server-fetch"] } }))
    written()

    await waitFor(() =>
      expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("git"),
    )
    expect(pasteBox.readOnly).toBe(false)
  })

  it("skips a server of a pasted block", async () => {
    const { caller, user } = openServersPage()
    const block = {
      github: { url: "https://api.githubcopilot.com/mcp/" },
      git: { command: "uvx", args: ["mcp-server-git"] },
    }

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.click(screen.getByRole("textbox", { name: "Paste an entry" }))
    await user.paste(JSON.stringify(block))
    await user.click(screen.getByRole("button", { name: "Skip" }))

    expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("git")
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull()
    await user.click(screen.getByRole("button", { name: "Add" }))
    expect(await screen.findByRole("button", { name: "git, written MCP server" })).toBeTruthy()
    expect(
      serverWrites(caller).map((call) => (call.params as LibraryMcpServerWriteCommand).name),
    ).toEqual(["git"])
  })

  it("shows the hub's reason for refusing a pasted entry beside the field it names", async () => {
    const reason = "kinby runs a local server with uvx only, not npx."
    const { user } = openServersPage({
      write: () => {
        throw refused({ "server.entry.stdio.command": reason })
      },
    })

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "Name" }), "acme")
    await user.click(screen.getByRole("textbox", { name: "Paste an entry" }))
    await user.paste('"acme": {"command": "npx", "args": ["@acme/server"]}')
    await user.click(screen.getByRole("button", { name: "Add" }))

    const command = await screen.findByRole("textbox", { name: "Command", description: reason })
    expect(command.getAttribute("aria-invalid")).toBe("true")
    expect(screen.queryByText("The hub refused it")).toBeNull()
  })

  it("says pasted text is not an entry or a block, and leaves the form as it was", async () => {
    const { user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "Add MCP server" }))
    await user.type(screen.getByRole("textbox", { name: "URL" }), "https://example.com/mcp")
    await user.click(screen.getByRole("textbox", { name: "Paste an entry" }))
    await user.paste('{"type": "sse", "url": "https://example.com/sse"}')

    expect(
      screen.getByText("This is not an http or stdio entry, nor an mcpServers block of them."),
    ).toBeTruthy()
    expect((screen.getByRole("textbox", { name: "URL" }) as HTMLInputElement).value).toBe(
      "https://example.com/mcp",
    )
  })

  it("copies a server as an mcpServers entry, without kinby's own fields", async () => {
    const { user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "fetch, written MCP server" }))
    await user.click(await screen.findByRole("button", { name: "Copy as JSON" }))

    expect(JSON.parse(await navigator.clipboard.readText())).toEqual({
      mcpServers: { fetch: fetchServer.entry },
    })
  })

  it("opens a server's definition and saves an edit over the one it read", async () => {
    const { caller, user } = openServersPage()

    await user.click(await screen.findByRole("button", { name: "fetch, written MCP server" }))
    const args = await screen.findByRole("textbox", { name: "Arguments" })
    expect((args as HTMLTextAreaElement).value).toBe("mcp-server-fetch\n--ignore-robots")
    expect((screen.getByRole("textbox", { name: "Env" }) as HTMLTextAreaElement).value).toBe(
      "FETCH_USER_AGENT=${FETCH_USER_AGENT}",
    )
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
    await screen.findByRole("textbox", { name: "Command" })
    await user.click(screen.getByRole("button", { name: "Delete" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Delete fetch?" })
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "fetch, written MCP server" })).toBeNull(),
    )
    expect(serverWrites(caller)).toEqual([
      { method: "library.mcp_server.delete", params: { name: "fetch", hash: "h-1" } },
    ])
    expect(screen.queryByRole("textbox", { name: "Command" })).toBeNull()
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
      entry: {
        type: "stdio",
        command: "uvx",
        args: ["odd", "", " --flag ", "two\nlines"],
        env: {},
      },
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
