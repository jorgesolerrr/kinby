import {
  CallError,
  type ConnectionState,
  type InstanceClient,
  type Method,
  type PermissionMode,
  type ThreadSummary,
} from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { NavThreads } from "@/components/nav-threads"
import { ThreadHeader } from "@/components/thread-header"
import { SidebarMenu, SidebarMenuItem, SidebarProvider } from "@/components/ui/sidebar"
import { threadList } from "@/lib/thread-list"

function thread(fields: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    id: "t1",
    title: "Deploy notes",
    created_at: "2026-09-27T10:00:00Z",
    last_activity_at: "2026-09-27T10:00:00Z",
    status: "idle",
    mode: "ask",
    mode_pinned: false,
    ...fields,
  }
}

/** `caller` as an instance client whose socket is up. */
function connected<Caller>(caller: Caller) {
  return { ...caller, state: () => "connected" as const, onStateChange: () => () => {} }
}

/**
 * Open thread t1's header once its instance has listed `listed()` under `ceiling`. The list is read
 * again after each change, so a test changes what `listed` returns to show what the instance did.
 */
async function openHeader(
  answers: Answers & { listed?: () => ThreadSummary; ceiling?: PermissionMode } = {},
) {
  const { listed = () => thread(), ceiling = "full-access", ...calls } = answers
  const client = connected(
    stubCaller({ "thread.list": () => ({ threads: [listed()], ceiling }), ...calls }),
  )
  await act(() => threadList(client).list())
  render(<ThreadHeader client={client} threadId="t1" />)
  return client
}

/**
 * Open thread t1's header over a client whose changes stay out until the test fails them, as they
 * do while the socket is down but not yet closed.
 */
async function openHeaderOffline() {
  const lister = stubCaller({
    "thread.list": () => ({ threads: [thread()], ceiling: "full-access" }),
  })
  const sent: Method[] = []
  let fail = (_error: CallError) => {}
  const client = connected<Pick<InstanceClient, "call">>({
    call: (method, params) => {
      if (method === "thread.list") return lister.call(method, params)
      sent.push(method)
      return new Promise<never>((_resolve, reject) => (fail = reject))
    },
  })
  await act(() => threadList(client).list())
  render(<ThreadHeader client={client} threadId="t1" />)
  const lost = new CallError({
    code: "CONNECTION_LOST",
    message: "The connection to the instance dropped.",
    retryable: true,
  })
  return { sent, loseConnection: () => act(async () => fail(lost)) }
}

const modePicker = () => screen.getByRole("combobox", { name: "Mode" })

describe("a thread's header", () => {
  it("names a thread without a title as untitled", async () => {
    await openHeader({ listed: () => thread({ title: null }) })

    expect(screen.getByRole("heading").textContent).toBe("Untitled thread")
  })

  it("shows the thread's mode and disables the modes above the instance's ceiling", async () => {
    await openHeader({ ceiling: "ask" })
    const user = userEvent.setup()

    expect(modePicker().textContent).toContain("Ask")
    await user.click(modePicker())
    await screen.findAllByRole("option")
    const option = (name: RegExp) => screen.getByRole("option", { name })
    const modes = [/^Read-only/, /^Ask/, /^Auto/, /^Full access/].map(option)

    expect(modes.map((mode) => mode.getAttribute("aria-disabled") === "true")).toEqual([
      false,
      false,
      true,
      true,
    ])
    expect(
      modes.map((mode) => mode.textContent?.includes("Above this instance's ceiling")),
    ).toEqual([false, false, true, true])
  })

  it("pins the mode picked and shows it once the instance lists it", async () => {
    let mode: PermissionMode = "ask"
    const client = await openHeader({
      listed: () => thread({ mode, mode_pinned: mode !== "ask" }),
      "thread.mode.set": (params) => {
        mode = params.mode
        return { thread_id: "t1", turn_id: "pin", sequence: 1 }
      },
    })
    const user = userEvent.setup()

    await user.click(modePicker())
    await user.click(await screen.findByRole("option", { name: /^Read-only/ }))

    expect(client.calls).toContainEqual({
      method: "thread.mode.set",
      params: { thread_id: "t1", mode: "read-only" },
    })
    expect(modePicker().textContent).toContain("Read-only")
  })

  it("renames the thread in place", async () => {
    let title = "Deploy notes"
    const client = await openHeader({
      listed: () => thread({ title }),
      "thread.rename": (params) => {
        title = params.title
        return thread({ title })
      },
    })
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deploy notes" }))
    const field = screen.getByRole("textbox", { name: "Thread title" })
    await user.clear(field)
    await user.type(field, "Release plan{Enter}")

    expect(client.calls).toContainEqual({
      method: "thread.rename",
      params: { thread_id: "t1", title: "Release plan" },
    })
    expect(screen.queryByRole("textbox")).toBeNull()
    expect(screen.getByRole("heading").textContent).toBe("Release plan")
  })

  it("gives focus back to the title once a rename typed from the keyboard is in", async () => {
    let title = "Deploy notes"
    await openHeader({
      listed: () => thread({ title }),
      "thread.rename": (params) => {
        title = params.title
        return thread({ title })
      },
    })
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deploy notes" }))
    await user.keyboard(" v2{Enter}")

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Deploy notes v2" }))
  })

  it("gives focus back to the title when Enter leaves it as it was", async () => {
    await openHeader()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deploy notes" }))
    await user.keyboard("{Enter}")

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Deploy notes" }))
  })

  it("keeps the title when the rename is cancelled, and gives focus back to it", async () => {
    const client = await openHeader()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deploy notes" }))
    await user.type(screen.getByRole("textbox", { name: "Thread title" }), " v2{Escape}")

    expect(client.calls.map((call) => call.method)).not.toContain("thread.rename")
    expect(screen.getByRole("heading").textContent).toBe("Deploy notes")
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Deploy notes" }))
  })

  it("leaves focus where it went when leaving the title closes it", async () => {
    await openHeader()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deploy notes" }))
    await user.click(modePicker())

    expect(screen.queryByRole("textbox", { name: "Thread title" })).toBeNull()
    expect(document.activeElement).toBe(modePicker())
  })

  it("shows why the instance refused a rename, and keeps the title being typed in focus", async () => {
    await openHeader({
      "thread.rename": () => {
        throw new CallError({
          code: "INVALID_ARGUMENT",
          message: "The title is too long.",
          retryable: false,
        })
      },
    })
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deploy notes" }))
    await user.type(screen.getByRole("textbox", { name: "Thread title" }), " v2{Enter}")

    expect((await screen.findByRole("alert")).textContent).toContain("The title is too long.")
    const field = screen.getByRole("textbox", { name: "Thread title" })
    expect(field).toHaveProperty("value", "Deploy notes v2")
    expect(document.activeElement).toBe(field)
  })

  it("holds the new title while the rename is out, and shows why once it fails", async () => {
    const { sent, loseConnection } = await openHeaderOffline()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deploy notes" }))
    const field = screen.getByRole("textbox", { name: "Thread title" })
    await user.type(field, " v2{Enter}")
    await user.type(field, "{Enter}{Escape}")

    expect(sent).toEqual(["thread.rename"])
    expect(field).toHaveProperty("readOnly", true)
    expect(field).toHaveProperty("value", "Deploy notes v2")
    expect(screen.getByRole("status", { name: "Renaming" })).toBeDefined()

    await loseConnection()

    expect(screen.getByRole("alert").textContent).toContain(
      "The connection to the instance dropped.",
    )
    expect(screen.queryByRole("status", { name: "Renaming" })).toBeNull()
    expect(field).toHaveProperty("readOnly", false)
    expect(field).toHaveProperty("value", "Deploy notes v2")
  })

  it("holds the mode picker in focus while the change is out, and shows why once it fails", async () => {
    const { sent, loseConnection } = await openHeaderOffline()
    const user = userEvent.setup()

    act(() => modePicker().focus())
    await user.keyboard("{ArrowDown}")
    await screen.findAllByRole("option")
    await user.keyboard("{ArrowUp}{Enter}")

    expect(sent).toEqual(["thread.mode.set"])
    expect(modePicker().hasAttribute("disabled")).toBe(false)
    expect(modePicker().getAttribute("aria-readonly")).toBe("true")
    expect(document.activeElement).toBe(modePicker())
    expect(screen.getByRole("status", { name: "Changing the mode" })).toBeDefined()

    await user.click(modePicker())
    await user.click(await screen.findByRole("option", { name: /^Full access/ }))

    expect(sent).toEqual(["thread.mode.set"])

    await loseConnection()

    expect(screen.getByRole("alert").textContent).toContain(
      "The connection to the instance dropped.",
    )
    expect(screen.queryByRole("status", { name: "Changing the mode" })).toBeNull()
    expect(modePicker().hasAttribute("aria-readonly")).toBe(false)
    expect(modePicker().textContent).toContain("Ask")
  })

  describe("without the sidebar", () => {
    it("lists the threads itself when nothing has listed them", async () => {
      const client = connected(
        stubCaller({ "thread.list": () => ({ threads: [thread()], ceiling: "full-access" }) }),
      )

      render(<ThreadHeader client={client} threadId="t1" />)

      expect((await screen.findByRole("heading")).textContent).toBe("Deploy notes")
      expect(modePicker().textContent).toContain("Ask")
    })

    it("lists the threads again when the last list came before the thread", async () => {
      let threads: ThreadSummary[] = []
      const client = connected(
        stubCaller({ "thread.list": () => ({ threads, ceiling: "full-access" }) }),
      )
      await act(() => threadList(client).list())

      threads = [thread()]
      render(<ThreadHeader client={client} threadId="t1" />)

      expect((await screen.findByRole("heading")).textContent).toBe("Deploy notes")
    })

    it("lists the threads once the instance connects", async () => {
      const lister = stubCaller({
        "thread.list": () => ({ threads: [thread()], ceiling: "full-access" }),
      })
      let state: ConnectionState = "connecting"
      const listeners = new Set<() => void>()
      // Like the instance client, it refuses a call before the socket is up.
      const client: Pick<InstanceClient, "call" | "state" | "onStateChange"> = {
        call: (method, params) =>
          state === "connected"
            ? lister.call(method, params)
            : Promise.reject(
                new CallError({
                  code: "CONNECTION_LOST",
                  message: "Not connected.",
                  retryable: true,
                }),
              ),
        state: () => state,
        onStateChange: (listener) => {
          listeners.add(listener)
          return () => listeners.delete(listener)
        },
      }
      render(<ThreadHeader client={client} threadId="t1" />)

      await act(async () => {
        state = "connected"
        for (const listener of listeners) listener()
      })

      expect((await screen.findByRole("heading")).textContent).toBe("Deploy notes")
      expect(modePicker().textContent).toContain("Ask")
    })

    it("stays empty when the threads do not list", async () => {
      const client = connected(stubCaller({}))

      await act(async () => {
        render(<ThreadHeader client={client} threadId="t1" />)
      })

      expect(client.calls.map((call) => call.method)).toEqual(["thread.list"])
      expect(screen.queryByRole("heading")).toBeNull()
    })
  })

  it("leaves listing the threads again to the sidebar beside it", async () => {
    const client = connected(
      stubCaller({ "thread.list": () => ({ threads: [thread()], ceiling: "full-access" }) }),
    )
    const clock = fakeClock()
    render(
      <SidebarProvider>
        <SidebarMenu>
          <SidebarMenuItem>
            <NavThreads client={client} clock={clock} instanceId="hub-ada" />
          </SidebarMenuItem>
        </SidebarMenu>
        <ThreadHeader client={client} threadId="t1" />
      </SidebarProvider>,
    )
    await screen.findByRole("heading")
    const listings = () => client.calls.filter((call) => call.method === "thread.list").length
    const onMount = listings()

    for (const _ of [1, 2, 3]) await act(() => clock.advance(5_000))

    expect(onMount).toBeLessThanOrEqual(2)
    expect(listings() - onMount).toBe(3)
  })
})
