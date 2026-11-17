import type { McpServer } from "@kinby/contract"

/** An MCP server's definition as its form holds it, each list one item a line. */
export interface McpServerDraft {
  type: "http" | "stdio"
  url: string
  /** One `Name: value` a line. */
  headers: string
  command: string
  args: string
  /** The arguments as read, which `args` cannot always show: an empty one, or one with a newline. */
  readArgs: string[]
  /** One `NAME=value` a line. */
  env: string
  include: string
}

export const EMPTY_DRAFT: McpServerDraft = {
  type: "http",
  url: "",
  headers: "",
  command: "uvx",
  args: "",
  readArgs: [],
  env: "",
  include: "",
}

export function draftOf({ entry, include = [] }: McpServer): McpServerDraft {
  const tools = include.join("\n")
  return entry.type === "http"
    ? { ...EMPTY_DRAFT, include: tools, url: entry.url, headers: textOf(entry.headers, ": ") }
    : {
        ...EMPTY_DRAFT,
        include: tools,
        type: "stdio",
        command: entry.command,
        args: (entry.args ?? []).join("\n"),
        readArgs: entry.args ?? [],
        env: textOf(entry.env, "="),
      }
}

export function serverOf(draft: McpServerDraft): McpServer {
  return {
    entry:
      draft.type === "http"
        ? { type: "http", url: draft.url.trim(), headers: pairsOf(draft.headers, ":") }
        : {
            type: "stdio",
            command: draft.command.trim(),
            args: draft.args === draft.readArgs.join("\n") ? draft.readArgs : lines(draft.args),
            env: pairsOf(draft.env, "="),
          },
    include: lines(draft.include),
  }
}

function textOf(pairs: Record<string, string> = {}, separator: string): string {
  return Object.entries(pairs)
    .map(([name, value]) => `${name}${separator}${value}`)
    .join("\n")
}

function pairsOf(text: string, separator: string): Record<string, string> {
  return Object.fromEntries(
    lines(text).map((line) => {
      const [name = "", ...value] = line.split(separator)
      return [name.trim(), value.join(separator).trim()]
    }),
  )
}

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
}
