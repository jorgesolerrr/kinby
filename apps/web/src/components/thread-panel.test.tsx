import { CallError, type Event, type InstanceSummary } from "@kinby/contract"
import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it } from "vitest"

import { openThread, started, thread } from "@/components/thread-testing"

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

    it("shows nothing above the composer when the instance has none", async () => {
      const { subscription } = openThread()
      await act(async () => subscription().subscribed(0))

      expect(screen.getByRole("textbox", { name: "Message Ada" })).toBeDefined()
      expect(screen.queryByRole("alert")).toBeNull()
    })
  })
})
