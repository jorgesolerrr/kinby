import type { Event } from "@kinby/contract"
import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { openThread, type Payload, started, thread, traceLine } from "@/components/thread-testing"

const call = (callId: string, name: string, args: Record<string, string> = {}): Payload => ({
  type: "tool.call",
  call_id: callId,
  name,
  arguments: args,
})

const gated = (
  callId: string,
  name: string,
  {
    action = "allow",
    decidedBy = "policy",
  }: { action?: "allow" | "deny"; decidedBy?: "policy" | "user" } = {},
): Payload => ({
  type: "tool.gated",
  call_id: callId,
  name,
  action,
  decided_by: decidedBy,
  rule: decidedBy === "user" ? "mode.ask.write" : "reads are allowed",
})

const result = (
  callId: string,
  name: string,
  {
    output = "",
    error = false,
    durationMs = 12,
  }: { output?: string; error?: boolean; durationMs?: number | null } = {},
): Payload => ({
  type: "tool.result",
  call_id: callId,
  name,
  output,
  error,
  duration_ms: durationMs,
})

const completed: Payload = { type: "turn.completed", input_tokens: 90, output_tokens: 10 }

/** Open a thread whose one turn records `payloads`. */
async function openTurn(...payloads: Payload[]) {
  const events: Event[] = thread(
    ["turn-1", started("Plan Friday")],
    ...payloads.map((payload): [string, Payload] => ["turn-1", payload]),
  )
  const opened = openThread()
  await act(async () => {
    opened.subscription().subscribed(events.length)
    for (const event of events) opened.subscription().deliver(event)
  })
  return opened
}

/** The events of calls to `names`, each allowed by policy and returning in 12 ms. */
const finished = (names: string[]): Payload[] =>
  names.flatMap((name, index) => [
    call(`c${index}`, name, { target: `${name}-target` }),
    gated(`c${index}`, name),
    result(`c${index}`, name),
  ])

describe("a thread's panel", () => {
  describe("a finished run of tool calls", () => {
    it.each([
      [["memory_search"], "Recalled what it knows"],
      [["web_search"], "Searched the web"],
      [["browser_open"], "Opened a page"],
      [["browser_open", "browser_open"], "Opened 2 pages"],
      [["browser_click"], "Used the browser once"],
      [["browser_click", "browser_type", "browser_snapshot"], "Used the browser 3 times"],
      [["bash"], "Used bash"],
      [["bash", "read"], "Used 2 tools"],
      [["web_search", "bash", "read"], "Searched the web, 2 more tools"],
      [
        ["memory_open", "web_search", "browser_open", "browser_open", "browser_click", "bash"],
        "Recalled what it knows, searched the web, opened 2 pages, used the browser once, 1 more tool",
      ],
    ])("of %j reads as one line: %s", async (names, words) => {
      await openTurn(...finished(names), completed)

      expect(screen.getByRole("button", { name: words })).toBeDefined()
      expect(screen.queryByText(`${names[0]} ${names[0]}-target`)).toBeNull()
    })

    it("counts the calls that failed, leaving out the ones the gate denied", async () => {
      await openTurn(
        call("c1", "read", { path: "a.md" }),
        gated("c1", "read"),
        result("c1", "read", { output: "No such file.", error: true }),
        call("c2", "read", { path: "b.md" }),
        gated("c2", "read"),
        result("c2", "read", { output: "No such file.", error: true }),
        call("c3", "write", { path: "c.md" }),
        gated("c3", "write", { action: "deny" }),
        result("c3", "write", { output: 'Tool "write" was denied by policy.', error: true }),
        completed,
      )

      expect(within(traceLine("Used 3 tools")).getByText("2 failed")).toBeDefined()
    })
  })

  describe("a run with a call in progress", () => {
    it.each([
      ["web_search", { query: "restaurants Alfama" }, "Searching “restaurants Alfama”"],
      ["memory_search", { query: "Friday" }, "Recalling what it knows"],
      ["browser_open", { url: "https://booking.com" }, "Opening https://booking.com"],
      ["browser_click", { selector: "#reserve" }, "Using the browser"],
      ["bash", { command: "make deploy" }, "Running bash make deploy"],
    ])("reads as the %s call in the present tense", async (name, args, words) => {
      await openTurn(...finished(["memory_search"]), call("c9", name, args), gated("c9", name))

      expect(screen.getByRole("button", { name: words })).toBeDefined()
    })

    it("reads as waiting for you while a call waits at the gate", async () => {
      await openTurn(
        call("c1", "read", { path: "deploy.log" }),
        call("c2", "bash", { command: "make deploy" }),
        {
          type: "approval.requested",
          approval_id: "a1",
          name: "bash",
          arguments: { command: "make deploy" },
          rule: "ask mode asks before commands",
        },
      )

      expect(screen.getByRole("button", { name: "Waiting for you to approve bash" })).toBeDefined()
    })
  })

  describe("an unfolded run", () => {
    it("shows one line per call, with its main argument, how the gate decided it and how long it took", async () => {
      await openTurn(
        call("c1", "read", { path: "runtime.py" }),
        gated("c1", "read"),
        result("c1", "read", { durationMs: 38 }),
        call("c2", "write", { path: "notes.md", content: "…" }),
        gated("c2", "write", { action: "deny" }),
        result("c2", "write", { output: 'Tool "write" was denied by policy.', error: true }),
        call("c3", "bash", { command: "make deploy" }),
        gated("c3", "bash", { decidedBy: "user" }),
        result("c3", "bash", { durationMs: 1_200 }),
        call("c4", "read", { path: "deploy.log" }),
        gated("c4", "read"),
        result("c4", "read", { durationMs: null }),
        completed,
      )

      await userEvent.click(traceLine("Used 4 tools"))

      for (const [line, decision] of [
        ["read runtime.py", "38 ms"],
        ["write notes.md", "denied by policy"],
        ["bash make deploy", "approved by you · 1,200 ms"],
        ["read deploy.log", "done"],
      ]) {
        expect(within(traceLine(line)).getByText(decision)).toBeDefined()
      }
    })

    it("marks a failed call as failed, with how long it took when the result says", async () => {
      await openTurn(
        call("c1", "hub_read", { path: "routine.list" }),
        gated("c1", "hub_read"),
        result("c1", "hub_read", { output: "Not available.", error: true, durationMs: null }),
        call("c2", "hub_read", { path: "view.list" }),
        gated("c2", "hub_read"),
        result("c2", "hub_read", { output: "Not available.", error: true }),
        call("c3", "bash", { command: "make deploy" }),
        gated("c3", "bash", { decidedBy: "user" }),
        result("c3", "bash", { output: "make: *** No rule.", error: true }),
        completed,
      )

      await userEvent.click(traceLine("Used 3 tools"))

      expect(within(traceLine("hub_read routine.list")).getByText("failed")).toBeDefined()
      expect(within(traceLine("hub_read view.list")).getByText("failed after 12 ms")).toBeDefined()
      expect(
        within(traceLine("bash make deploy")).getByText("approved by you · failed after 12 ms"),
      ).toBeDefined()
    })

    it("unfolds a call to its arguments and its output", async () => {
      await openTurn(
        call("c1", "bash", { command: "make deploy", cwd: "/srv/app" }),
        gated("c1", "bash"),
        result("c1", "bash", { output: "deployed to production" }),
        completed,
      )
      await userEvent.click(traceLine("Used bash"))

      await userEvent.click(traceLine("bash make deploy"))

      expect(screen.getByText('{ "command": "make deploy", "cwd": "/srv/app" }')).toBeDefined()
      expect(screen.getByText("deployed to production")).toBeDefined()
    })
  })
})
