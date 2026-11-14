import type { Client, Event, FactoryRunTranscript, JsonValue, StepId } from "@kinby/contract"

import { EMPTY_TIMELINE, mainArgument, project } from "@/lib/timeline"

type Streamed = { [k: string]: JsonValue }

/** One line of an attempt's transcript, whichever engine made it. */
export type Entry =
  | { kind: "say"; text: string }
  | { kind: "tool"; name: string; text: string }
  | { kind: "command"; text: string; exit: number | null }
  /** An event too large for the hub to hand over, by its size in bytes. */
  | { kind: "cut"; bytes: number }

/** Read every page of one attempt's transcript from the run's workbench, as its entries. */
export async function readTranscript(
  caller: Pick<Client, "call">,
  runId: string,
  step: StepId,
  attempt: number,
): Promise<Entry[]> {
  const events: Streamed[] = []
  let cursor: number | null = 0
  while (cursor !== null) {
    const page: FactoryRunTranscript = await caller.call("factory.run.transcript", {
      run_id: runId,
      step,
      attempt,
      cursor,
    })
    events.push(...page.events)
    cursor = page.cursor
  }
  return entries(events)
}

/**
 * An attempt's transcript as what its agent said and did, in order. A kinby turn's events read as
 * its thread would show them, and a coding client's stream, Claude Code's or Codex's, as the
 * messages, tools and commands in it. Anything else in a stream shows nothing.
 */
export function entries(events: Streamed[]): Entry[] {
  return events.some(isKinby) ? kinby(events as unknown as Event[]) : events.flatMap(streamed)
}

function isKinby(event: Streamed): boolean {
  return typeof event.turn_id === "string" && typeof event.payload === "object"
}

function kinby(events: Event[]): Entry[] {
  const { turns } = events.reduce(project, EMPTY_TIMELINE)
  return turns
    .flatMap(({ steps }) => steps)
    .flatMap((step): Entry[] => {
      switch (step.kind) {
        case "text":
          return [{ kind: "say", text: step.text }]
        case "tool":
          return [{ kind: "tool", name: step.name, text: mainArgument(step.arguments) ?? "" }]
        default:
          return []
      }
    })
}

function streamed(event: Streamed): Entry[] {
  if (event.type === "cut" && typeof event.bytes === "number") {
    return [{ kind: "cut", bytes: event.bytes }]
  }
  if (event.type === "assistant") return claude(event)
  if (event.type === "item.completed" && isObject(event.item)) return codex(event.item)
  return []
}

function claude(event: Streamed): Entry[] {
  const message = isObject(event.message) ? event.message : {}
  const content = Array.isArray(message.content) ? message.content : []
  return content.filter(isObject).flatMap((block): Entry[] => {
    if (block.type === "text" && typeof block.text === "string") {
      return [{ kind: "say", text: block.text }]
    }
    if (block.type === "tool_use" && typeof block.name === "string") {
      const input = isObject(block.input) ? block.input : {}
      return [{ kind: "tool", name: block.name, text: mainArgument(input) ?? "" }]
    }
    return []
  })
}

function codex(item: Streamed): Entry[] {
  switch (item.type) {
    case "agent_message":
      return typeof item.text === "string" ? [{ kind: "say", text: item.text }] : []
    case "command_execution":
      return [
        {
          kind: "command",
          text: text(item.command),
          exit: typeof item.exit_code === "number" ? item.exit_code : null,
        },
      ]
    case "file_change": {
      const changes = Array.isArray(item.changes) ? item.changes.filter(isObject) : []
      return [
        { kind: "tool", name: "edit", text: changes.map(({ path }) => text(path)).join(", ") },
      ]
    }
    case "mcp_tool_call":
      return [{ kind: "tool", name: text(item.tool), text: text(item.server) }]
    default:
      return []
  }
}

function text(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function isObject(value: unknown): value is Streamed {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
