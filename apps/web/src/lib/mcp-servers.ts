import type { McpServer, McpServerEntry } from "@kinby/contract"

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
  /** The env as read, which `env` cannot always show: a value with a newline or edge spaces. */
  readEnv: Record<string, string>
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
  readEnv: {},
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
        readEnv: entry.env ?? {},
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
            env: draft.env === textOf(draft.readEnv, "=") ? draft.readEnv : pairsOf(draft.env, "="),
          },
    include: lines(draft.include),
  }
}

/** A field of the MCP server form: the server's name, or one of its draft's. */
export type McpServerField = "name" | Exclude<keyof McpServerDraft, "type" | "readArgs" | "readEnv">

const DRAFT_FIELDS = new Set<string>(["url", "headers", "command", "args", "env"])

/**
 * The hub's reasons for refusing a write, by the form field each one's path names, such as
 * `server.entry.stdio.env.PATH.[key]` for env. A reason no field shows is under `other`.
 */
export function reasonsByField(
  fields: Record<string, string>,
): Partial<Record<McpServerField | "other", string>> {
  const reasons: Partial<Record<McpServerField | "other", string>> = {}
  for (const [path, reason] of Object.entries(fields)) {
    const field = fieldOf(path)
    reasons[field] = [reasons[field], reason].filter((text) => text !== undefined).join(" ")
  }
  return reasons
}

function fieldOf(path: string): McpServerField | "other" {
  const [top, value, , field = ""] = path.split(".")
  if (top === "name") return "name"
  if (top === "server" && value === "include") return "include"
  if (top === "server" && value === "entry" && DRAFT_FIELDS.has(field)) {
    return field as McpServerField
  }
  return "other"
}

/** A server pasted into the form: its entry, and its name when it came in a block. */
export interface PastedServer {
  name?: string
  entry: McpServerEntry
}

/**
 * The servers `text` holds, an `mcpServers` entry or a block of them, or undefined when it holds
 * neither. A block may come with `mcpServers` around it or bare, and as lines cut from one,
 * without the braces around them.
 */
export function pastedServers(text: string): PastedServer[] | undefined {
  const pasted = parsed(text) ?? parsed(`{${text}}`)
  if (!isRecord(pasted)) return undefined
  const entry = entryOf(pasted)
  if (entry !== undefined) return [{ entry }]
  const servers: PastedServer[] = []
  for (const [name, value] of Object.entries(
    isRecord(pasted.mcpServers) ? pasted.mcpServers : pasted,
  )) {
    const entry = entryOf(value)
    if (entry === undefined) return undefined
    servers.push({ name, entry })
  }
  return servers.length > 0 ? servers : undefined
}

/** `server`'s entry as JSON an MCP client takes, an `mcpServers` block holding it as `name`. */
export function copiedEntry(name: string, server: McpServer): string {
  return JSON.stringify({ mcpServers: { [name]: server.entry } }, null, 2)
}

/** An entry with no `type` is `http` when it has a `url`, as Cursor writes one. */
function entryOf(value: unknown): McpServerEntry | undefined {
  if (!isRecord(value)) return undefined
  const { url, headers = {}, command, args = [], env = {} } = value
  const { type = typeof url === "string" ? "http" : "stdio" } = value
  if (type === "http" && typeof url === "string" && isTextRecord(headers)) {
    return { type, url, headers }
  }
  if (type === "stdio" && typeof command === "string" && isTexts(args) && isTextRecord(env)) {
    return { type, command, args, env }
  }
  return undefined
}

function parsed(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isTextRecord(value: unknown): value is Record<string, string> {
  return isRecord(value) && isTexts(Object.values(value))
}

function isTexts(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
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
