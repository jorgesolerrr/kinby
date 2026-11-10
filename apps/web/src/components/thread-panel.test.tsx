import { CallError, type Event, type InstanceSummary } from "@kinby/contract"
import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it } from "vitest"

import { openThread, type Payload, started, thread } from "@/components/thread-testing"

const loading = () => screen.queryByRole("status", { name: "Loading the thread" })

describe("a thread's panel", () => {
  it("follows the thread after the sequence its store has, loading until the replay reaches the head", async () => {
    const events = thread(
      ["turn-1", started("Fix the runtime")],
      ["turn-1", { type: "message.delta", text: "On it." }],
    )
    const { client, subscription, unmount } = openThread()

    expect(subscription().params).toEqual({ thread_id: "t1", after_sequence: 0 })
    expect(loading()).not.toBeNull()

    await act(async () => {
      subscription().subscribed(2)
      subscription().deliver(events[0] as Event)
    })
    expect(loading()).not.toBeNull()

    await act(async () => subscription().deliver(events[1] as Event))
    expect(loading()).toBeNull()
    expect(screen.getByText("Fix the runtime")).toBeDefined()
    expect(screen.getByText("On it.")).toBeDefined()

    unmount()
    expect(subscription().cancelled).toBe(true)
    openThread({}, client)
    expect(subscription().params).toEqual({ thread_id: "t1", after_sequence: 2 })
  })

  it("says why a thread it cannot follow did not load", async () => {
    const { subscription } = openThread()

    await act(async () =>
      subscription().fail(
        new CallError({ code: "NOT_FOUND", message: "No thread t1.", retryable: false }),
      ),
    )

    expect(loading()).toBeNull()
    expect(screen.getByRole("alert").textContent).toContain("No thread t1.")
  })

  describe("the context size", () => {
    const called = (inputTokens: number, model = "claude"): Payload => ({
      type: "model.completed",
      model,
      input_tokens: inputTokens,
      output_tokens: 50,
      duration_ms: 900,
    })
    const completed: Payload = { type: "turn.completed", input_tokens: 1, output_tokens: 1 }
    const usage = (window: number) => ({
      threads: [{ thread_id: "t1", input_tokens: 0, output_tokens: 0, turns: [], window }],
    })
    const usageReads = (client: ReturnType<typeof openThread>["client"]) =>
      client.calls.filter((call) => call.method === "usage.get").length
    const bar = () => screen.getByRole("progressbar", { name: "Context" }).textContent

    it("reads the model's window once the replay reaches the head, not for each turn it replays", async () => {
      const events = thread(
        ["turn-1", started("Fix the runtime")],
        ["turn-1", called(12_000)],
        ["turn-1", completed],
        ["turn-2", started("Now the tests")],
        ["turn-2", called(14_500)],
        ["turn-2", completed],
      )
      const { client, subscription } = openThread({ "usage.get": () => usage(200_000) })
      await act(async () => subscription().subscribed(events.length))

      for (const event of events.slice(0, -1)) {
        await act(async () => subscription().deliver(event))
      }
      expect(usageReads(client)).toBe(0)

      await act(async () => subscription().deliver(events.at(-1) as Event))
      expect(usageReads(client)).toBe(1)
      expect(bar()).toContain("14.5k / 200k")
    })

    it("reads the window again when a model call runs on another model", async () => {
      const events = thread(
        ["turn-1", started("Fix the runtime")],
        ["turn-1", called(12_000)],
        ["turn-1", completed],
        ["turn-2", started("Now the tests")],
        ["turn-2", called(61_000, "gpt")],
      )
      let window = 200_000
      const { client, subscription } = openThread({ "usage.get": () => usage(window) })
      await act(async () => {
        subscription().subscribed(3)
        for (const event of events.slice(0, 3)) subscription().deliver(event)
      })
      expect(usageReads(client)).toBe(1)

      window = 1_000_000
      await act(async () => subscription().deliver(events[3] as Event))
      await act(async () => subscription().deliver(events[4] as Event))

      expect(usageReads(client)).toBe(2)
      expect(bar()).toContain("61k / 1m")
    })
  })

  describe("the instance's notices", () => {
    afterEach(() => window.history.replaceState(null, "", "/"))

    it("shows each one above the composer, opening the section of the instance's page that answers it", async () => {
      const notices: InstanceSummary["notices"] = [
        {
          code: "revision_behind",
          message: "The instance runs kinby 1a2b3c4, and the hub is at 5d6e7f8.",
          instance_revision: "1a2b3c4",
          hub_revision: "5d6e7f8",
        },
        { code: "mcp_server_failed", message: "uvx exited with code 1.", server: "github" },
      ]
      const { subscription } = openThread({}, undefined, undefined, undefined, notices)
      await act(async () => subscription().subscribed(0))
      const user = userEvent.setup()

      const [behind, failed] = screen.getAllByRole("alert")
      expect(behind?.textContent).toContain("Behind the hub")
      expect(behind?.textContent).toContain("and the hub is at 5d6e7f8.")
      expect(failed?.textContent).toContain("MCP server github failed")
      expect(failed?.textContent).toContain("uvx exited with code 1.")
      const composer = screen.getByRole("textbox", { name: "Message Ada" })
      expect(failed?.compareDocumentPosition(composer)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)

      await user.click(within(behind as HTMLElement).getByRole("button", { name: "Update" }))
      expect(window.location.pathname).toBe("/instances/hub-ada/config/core")
      await user.click(within(failed as HTMLElement).getByRole("button", { name: "Skills" }))
      expect(window.location.pathname).toBe("/instances/hub-ada/config/skills")
    })

    it("keeps one alert for each failed MCP server, even when two failed alike", async () => {
      const failed = (server: string) => ({
        code: "mcp_server_failed" as const,
        message: "uvx exited with code 1.",
        server,
      })
      const { relist, subscription } = openThread({}, undefined, undefined, undefined, [
        { code: "hub_route_missing", message: "The instance cannot reach the hub." },
        failed("github"),
        failed("slack"),
      ])
      await act(async () => subscription().subscribed(0))

      relist([failed("slack")])

      expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual([
        expect.stringContaining("MCP server slack failed"),
      ])
    })

    it("shows nothing above the composer when the instance has none", async () => {
      const { subscription } = openThread()
      await act(async () => subscription().subscribed(0))

      expect(screen.getByRole("textbox", { name: "Message Ada" })).toBeDefined()
      expect(screen.queryByRole("alert")).toBeNull()
    })
  })
})
