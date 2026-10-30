import {
  CallError,
  type ConnectionState,
  type InstanceClient,
  type Method,
  type PermissionMode,
  type ThreadFilter,
  type ThreadListCommand,
  type ThreadSummary,
} from "@kinby/contract"
import {
  type Answers,
  type StubCaller,
  fakeClock,
  instanceSummary,
  stubCaller,
} from "@kinby/contract/testing"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { NavInstances } from "@/components/nav-instances"
import { ThreadHeader } from "@/components/thread-header"
import { SidebarProvider } from "@/components/ui/sidebar"
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
    archived: false,
    origin: { kind: "user" },
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
    stubCaller({ "thread.list": () => ({ threads: [listed()], ceiling, cursor: null }), ...calls }),
  )
  await act(() => threadList(client, "sidebar").list())
  render(<ThreadHeader client={client} threadId="t1" />)
  return client
}

/**
 * Open thread t1's header over a client whose changes stay out until the test fails them, as they
 * do while the socket is down but not yet closed.
 */
async function openHeaderOffline() {
  const lister = stubCaller({
    "thread.list": () => ({ threads: [thread()], ceiling: "full-access", cursor: null }),
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
  await act(() => threadList(client, "sidebar").list())
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
      modes.map((mode) => mode.textContent?.includes("Above the ceiling. Raise it in Configure.")),
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
    await user.tab()

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
        stubCaller({
          "thread.list": () => ({ threads: [thread()], ceiling: "full-access", cursor: null }),
        }),
      )

      render(<ThreadHeader client={client} threadId="t1" />)

      expect((await screen.findByRole("heading")).textContent).toBe("Deploy notes")
      expect(modePicker().textContent).toContain("Ask")
    })

    it("lists the threads again when the last list came before the thread", async () => {
      let threads: ThreadSummary[] = []
      const client = connected(
        stubCaller({ "thread.list": () => ({ threads, ceiling: "full-access", cursor: null }) }),
      )
      await act(() => threadList(client, "sidebar").list())

      threads = [thread()]
      render(<ThreadHeader client={client} threadId="t1" />)

      expect((await screen.findByRole("heading")).textContent).toBe("Deploy notes")
    })

    it("lists the threads once the instance connects", async () => {
      const lister = stubCaller({
        "thread.list": () => ({ threads: [thread()], ceiling: "full-access", cursor: null }),
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

  describe("beside the sidebar", () => {
    const ada = instanceSummary({ instance_id: "hub-ada", persona_name: "Ada" })

    /**
     * Render thread t1's header beside the sidebar, with Ada selected. `poll` advances the
     * sidebar's clock to its next poll and returns the filters `thread.list` was called with
     * meanwhile.
     */
    function openBesideSidebar(client: StubCaller) {
      const clock = fakeClock()
      const instance = connected(client)
      render(
        <SidebarProvider>
          <NavInstances
            instances={[ada]}
            selected={ada}
            creating={false}
            client={{ instance: () => expect.unreachable() }}
            clock={clock}
            selectedClient={instance}
            folds={{}}
            onFold={() => {}}
          />
          <ThreadHeader client={instance} threadId="t1" />
        </SidebarProvider>,
      )
      const listings = () =>
        client.calls
          .filter((call) => call.method === "thread.list")
          .map((call) => (call.params as ThreadListCommand).filter)
      const poll = async () => {
        const before = listings().length
        await act(() => clock.advance(5_000))
        return listings().slice(before)
      }
      return { listings, poll }
    }

    /** An instance whose sidebar set holds `inSidebar()` and whose full set holds `inAll()`. */
    function listing(inSidebar: () => ThreadSummary[], inAll = inSidebar) {
      return stubCaller({
        "thread.list": ({ filter }) => ({
          threads: filter === "sidebar" ? inSidebar() : inAll(),
          ceiling: "full-access",
          cursor: null,
        }),
      })
    }

    it("lists only the sidebar's threads on each poll", async () => {
      const { poll } = openBesideSidebar(listing(() => [thread()]))
      await screen.findByRole("heading", { level: 1 })

      for (const _ of [1, 2, 3]) expect(await poll()).toEqual<ThreadFilter[]>(["sidebar"])
    })

    it("shows a title the instance sets after the next poll of the sidebar", async () => {
      let title: string | null = null
      const { poll } = openBesideSidebar(listing(() => [thread({ title })]))
      expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Untitled thread")

      title = "Deploy notes"

      expect(await poll()).toEqual<ThreadFilter[]>(["sidebar"])
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Deploy notes")
    })

    it.each([
      ["an archived thread", thread({ archived: true })],
      [
        "a quiet routine run",
        thread({ origin: { kind: "routine", name: "news", trigger: "scheduled" } }),
      ],
    ])("gives %s a header from one listing of every thread", async (_, outside) => {
      const { listings, poll } = openBesideSidebar(
        listing(
          () => [],
          () => [outside],
        ),
      )

      expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Deploy notes")
      expect(listings().filter((filter) => filter === "all")).toHaveLength(1)
      expect(await poll()).toEqual<ThreadFilter[]>(["sidebar"])
    })

    it("renames the thread in the sidebar without waiting for a poll", async () => {
      let title = "Deploy notes"
      openBesideSidebar(
        stubCaller({
          "thread.list": () => ({
            threads: [thread({ title })],
            ceiling: "full-access",
            cursor: null,
          }),
          "thread.rename": (params) => {
            title = params.title
            return thread({ title })
          },
        }),
      )
      const user = userEvent.setup()

      await user.click(await screen.findByRole("button", { name: "Deploy notes" }))
      await user.keyboard(" v2{Enter}")

      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Deploy notes v2")
      expect(screen.getByRole("link", { name: "Deploy notes v2" })).toBeDefined()
    })

    it("archives the thread out of the sidebar, and unarchives it back", async () => {
      let archived = false
      const put = (to: boolean) => () => {
        archived = to
        return thread({ archived })
      }
      const client = stubCaller({
        "thread.list": ({ filter }) => ({
          threads: filter === "sidebar" && archived ? [] : [thread({ archived })],
          ceiling: "full-access",
          cursor: null,
        }),
        "thread.archive": put(true),
        "thread.unarchive": put(false),
      })
      openBesideSidebar(client)
      const user = userEvent.setup()
      await screen.findByRole("link", { name: "Deploy notes" })

      await user.click(await screen.findByRole("button", { name: "Archive" }))

      expect(client.calls).toContainEqual({ method: "thread.archive", params: { thread_id: "t1" } })
      expect(screen.queryByRole("link", { name: "Deploy notes" })).toBeNull()
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Deploy notes")

      await user.click(screen.getByRole("button", { name: "Unarchive" }))

      expect(client.calls).toContainEqual({
        method: "thread.unarchive",
        params: { thread_id: "t1" },
      })
      expect(screen.getByRole("link", { name: "Deploy notes" })).toBeDefined()
      expect(screen.getByRole("button", { name: "Archive" })).toBeDefined()
    })

    it("keeps focus on the archive button when a poll answers before the full list", async () => {
      let archived = false
      const instance = stubCaller({
        "thread.list": ({ filter }) => ({
          threads: filter === "sidebar" && archived ? [] : [thread({ archived })],
          ceiling: "full-access",
          cursor: null,
        }),
        "thread.archive": () => {
          archived = true
          return thread({ archived })
        },
      })
      const held: (() => void)[] = []
      const { poll } = openBesideSidebar({
        calls: instance.calls,
        call: async (method, params) => {
          if (archived && (params as ThreadListCommand).filter === "all") {
            await new Promise<void>((resolve) => held.push(resolve))
          }
          return instance.call(method, params)
        },
      })
      const user = userEvent.setup()

      await user.click(await screen.findByRole("button", { name: "Archive" }))
      await poll()
      await act(async () => {
        for (const release of held) release()
      })

      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Unarchive" }))
    })

    it("shows the thread of a core from before archiving, which refuses the filter", async () => {
      openBesideSidebar(
        stubCaller({
          "thread.list": (params) => {
            if ("filter" in params) {
              throw new CallError({
                code: "INVALID_ARGUMENT",
                message: "filter: Extra inputs are not permitted",
                retryable: false,
              })
            }
            return { threads: [thread()], ceiling: "full-access", cursor: null }
          },
        }),
      )

      expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Deploy notes")
      expect(screen.getByRole("link", { name: "Deploy notes" })).toBeDefined()
    })
  })
})
