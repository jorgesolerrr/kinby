import { describe, expect, it } from "vitest"

import { started, thread } from "@/components/thread-testing"
import { entries } from "@/lib/run-transcript"

describe("an attempt's transcript", () => {
  it("reads a kinby turn's events as what it said and the tools it called", () => {
    const events = thread(
      ["turn-1", started("Draft unit 3")],
      ["turn-1", { type: "message.delta", text: "Reading " }],
      ["turn-1", { type: "message.delta", text: "the brief." }],
      ["turn-1", { type: "tool.call", call_id: "c1", name: "read", arguments: { path: "u3.md" } }],
      ["turn-1", { type: "message.delta", text: "Drafted." }],
    )

    // The hub hands them over as JSON.
    expect(entries(JSON.parse(JSON.stringify(events)))).toEqual([
      { kind: "say", text: "Reading the brief." },
      { kind: "tool", name: "read", text: "u3.md" },
      { kind: "say", text: "Drafted." },
    ])
  })

  it("reads a resumed kinby turn, which starts with no turn.started, and an event too large to read", () => {
    const events = thread(
      ["turn-1", { type: "message.delta", text: "Signed in." }],
      ["turn-1", { type: "tool.call", call_id: "c2", name: "browse", arguments: { url: "a.edu" } }],
    )

    expect(
      entries([...JSON.parse(JSON.stringify(events)), { type: "cut", bytes: 5_000_000 }]),
    ).toEqual([
      { kind: "say", text: "Signed in." },
      { kind: "tool", name: "browse", text: "a.edu" },
      { kind: "cut", bytes: 5_000_000 },
    ])
  })

  it("reads Claude Code's stream as what it said and the tools it used", () => {
    const events = [
      { type: "system", subtype: "init", session_id: "s-1" },
      {
        type: "assistant",
        message: {
          content: [
            { type: "text", text: "Reading the ticket." },
            { type: "tool_use", id: "t1", name: "Bash", input: { command: "gh issue view 12" } },
          ],
        },
      },
      { type: "user", message: { content: [{ type: "tool_result", tool_use_id: "t1" }] } },
      { type: "result", subtype: "success", result: "Done." },
    ]

    expect(entries(events)).toEqual([
      { kind: "say", text: "Reading the ticket." },
      { kind: "tool", name: "Bash", text: "gh issue view 12" },
    ])
  })

  it("reads Codex's stream as what it said, the commands it ran with their exit, and the files it changed", () => {
    const events = [
      { type: "thread.started", thread_id: "th-1" },
      { type: "item.started", item: { id: "i0", type: "command_execution", command: "ls" } },
      {
        type: "item.completed",
        item: { id: "i0", type: "command_execution", command: "ls", exit_code: 0 },
      },
      { type: "item.completed", item: { id: "i1", type: "reasoning", text: "Thinking." } },
      {
        type: "item.completed",
        item: { id: "i2", type: "command_execution", command: "pytest", exit_code: 1 },
      },
      {
        type: "item.completed",
        item: { id: "i3", type: "file_change", changes: [{ path: "a.py", kind: "update" }] },
      },
      { type: "item.completed", item: { id: "i4", type: "agent_message", text: "Fixed it." } },
      { type: "turn.completed", usage: { input_tokens: 10, output_tokens: 2 } },
    ]

    expect(entries(events)).toEqual([
      { kind: "command", text: "ls", exit: 0 },
      { kind: "command", text: "pytest", exit: 1 },
      { kind: "tool", name: "edit", text: "a.py" },
      { kind: "say", text: "Fixed it." },
    ])
  })

  it("says how large an event too large to read was", () => {
    expect(entries([{ type: "cut", bytes: 5_000_000 }])).toEqual([
      { kind: "cut", bytes: 5_000_000 },
    ])
  })
})
