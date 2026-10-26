import type { McpServer } from "@kinby/contract"

/** An MCP server's definition as its form holds it, each list one item a line. */
export interface McpServerDraft {
  transport: "url" | "uvx"
  url: string
  /** One `Name: VARIABLE` a line. */
  headers: string
  package: string
  args: string
  env: string
  include: string
}

export const EMPTY_DRAFT: McpServerDraft = {
  transport: "url",
  url: "",
  headers: "",
  package: "",
  args: "",
  env: "",
  include: "",
}

export function draftOf({ transport, env = [], include = [] }: McpServer): McpServerDraft {
  const lists = { env: env.join("\n"), include: include.join("\n") }
  return transport.type === "url"
    ? {
        ...EMPTY_DRAFT,
        ...lists,
        url: transport.url,
        headers: Object.entries(transport.headers ?? {})
          .map(([name, variable]) => `${name}: ${variable}`)
          .join("\n"),
      }
    : {
        ...EMPTY_DRAFT,
        ...lists,
        transport: "uvx",
        package: transport.package,
        args: (transport.args ?? []).join("\n"),
      }
}

export function serverOf(draft: McpServerDraft): McpServer {
  return {
    transport:
      draft.transport === "url"
        ? {
            type: "url",
            url: draft.url.trim(),
            headers: Object.fromEntries(
              lines(draft.headers).map((line) => {
                const [name = "", ...variable] = line.split(":")
                return [name.trim(), variable.join(":").trim()]
              }),
            ),
          }
        : { type: "uvx", package: draft.package.trim(), args: lines(draft.args) },
    env: lines(draft.env),
    include: lines(draft.include),
  }
}

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
}
