import type { Event } from "@kinby/contract"
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest"

import { openThread, type Payload, started, thread } from "@/components/thread-testing"

describe("a thread's panel", () => {
  it("marks who started each turn, each tool call's main argument and gate decision, and how the turn ended", async () => {
    const gated = (
      callId: string,
      name: string,
      action: "allow" | "deny",
      decidedBy: "policy" | "user",
      rule: string,
    ): Payload => ({
      type: "tool.gated",
      call_id: callId,
      name,
      action,
      decided_by: decidedBy,
      rule,
    })
    const call = (callId: string, name: string, args: Record<string, string>): Payload => ({
      type: "tool.call",
      call_id: callId,
      name,
      arguments: args,
    })
    const events = thread(
      ["turn-1", started("Fix the runtime")],
      ["turn-1", call("c1", "read", { path: "runtime.py" })],
      ["turn-1", call("c2", "write", { path: "notes.md", content: "…" })],
      ["turn-1", gated("c1", "read", "allow", "policy", "reads are allowed")],
      ["turn-1", gated("c2", "write", "deny", "policy", "read-only mode denies writes")],
      [
        "turn-1",
        {
          type: "tool.result",
          call_id: "c1",
          name: "read",
          output: "",
          error: false,
          duration_ms: 38,
        },
      ],
      ["turn-1", call("c3", "edit", { path: "runtime.py", old: "a", new: "b" })],
      ["turn-1", gated("c3", "edit", "allow", "user", "ask mode asks before writes")],
      ["turn-1", { type: "turn.completed", input_tokens: 1_200, output_tokens: 300 }],
      [
        "turn-2",
        {
          type: "turn.started",
          message: "Summarize the night's email",
          model: "claude",
          origin: { kind: "routine", name: "nightly-digest", trigger: "scheduled" },
        },
      ],
      [
        "turn-2",
        { type: "turn.failed", code: "BUDGET_EXCEEDED", message: "The steps budget ran out." },
      ],
      ["turn-3", started("Deploy it")],
      ["turn-3", { type: "turn.interrupted" }],
      ["turn-4", started("Deploy it now")],
      ["turn-4", call("c5", "read", { path: "deploy.log" })],
      ["turn-4", call("c4", "bash", { command: "make deploy" })],
      [
        "turn-4",
        {
          type: "approval.requested",
          approval_id: "a1",
          name: "bash",
          arguments: { command: "make deploy" },
          rule: "ask mode asks before commands",
        },
      ],
    )
    const { subscription } = openThread()

    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    for (const marker of [
      "read runtime.py · 38 ms",
      "write notes.md · denied by policy: read-only mode denies writes",
      "edit runtime.py · approved by you · running",
      "Done · 3 steps · 1,500 tokens",
      "Failed: The steps budget ran out. (BUDGET_EXCEEDED). Send a message to try again.",
      "Stopped",
      "read deploy.log · running",
      "bash make deploy · waiting for you",
      "Working",
    ]) {
      expect(screen.getByText(marker)).toBeDefined()
    }
    expect(screen.getByText(/^Routine nightly-digest ·/)).toBeDefined()
  })

  describe("the line a turn opens with", () => {
    async function opened(origin: Extract<Payload, { type: "turn.started" }>["origin"]) {
      const events = thread([
        "turn-1",
        { type: "turn.started", message: "Check my job applications.", model: "claude", origin },
      ])
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
    }

    it("names the routine that started the turn", async () => {
      await opened({ kind: "routine", name: "nightly-digest", trigger: "scheduled" })

      expect(screen.getByText(/^Routine nightly-digest ·/)).toBeDefined()
    })

    it("says the home instance started the turn", async () => {
      await opened({ kind: "home-instance" })

      expect(screen.getByText(/^Home instance ·/)).toBeDefined()
    })

    it("says a factory run started the turn", async () => {
      await opened({ kind: "factory-run", factory: "software", run_id: "r1", step: "implement" })

      expect(screen.getByText(/^Factory software · implement ·/)).toBeDefined()
    })

    it("is not there on a turn the user started", async () => {
      await opened({ kind: "user" })

      expect(screen.getByText("Check my job applications.")).toBeDefined()
      expect(screen.queryByText(/^You ·/)).toBeNull()
    })
  })

  it("names the browser's zone in the time a routine's turn started", async () => {
    vi.stubEnv("TZ", "America/Bogota")
    onTestFinished(() => void vi.unstubAllEnvs())
    const events = thread([
      "turn-1",
      {
        type: "turn.started",
        message: "Summarize the night's email",
        model: "claude",
        origin: { kind: "routine", name: "nightly-digest", trigger: "scheduled" },
      },
    ])
    const { subscription } = openThread()

    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    const bogota = new Intl.DateTimeFormat("en", {
      timeZone: "America/Bogota",
      timeZoneName: "short",
    })
      .formatToParts(new Date("2026-09-28T10:00:00Z"))
      .find((part) => part.type === "timeZoneName")?.value
    if (bogota === undefined) throw new Error("America/Bogota has no short zone name")
    const time =
      screen.getByText(/^Routine nightly-digest ·/).querySelector("time")?.textContent ?? ""
    expect(time).toContain(bogota)
  })

  describe("a turn the URL names", () => {
    const turns = ["turn-1", "turn-2", "turn-3"]

    // jsdom lays nothing out and cannot scroll. Here each turn stands 1,000 px tall below the one
    // before it, scrolling the transcript moves them up, and the thread opens on the last at 2,000.
    beforeEach(() => {
      vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
        this: Element,
      ) {
        const index = turns.indexOf(this.getAttribute("data-message-id") ?? "")
        if (index === -1) return DOMRect.fromRect()
        const scrolled = this.closest("[data-slot=message-scroller-viewport]")?.scrollTop ?? 0
        return DOMRect.fromRect({ y: index * 1_000 - scrolled, height: 1_000 })
      })
      Object.defineProperty(Element.prototype, "scrollTo", {
        configurable: true,
        value(this: Element, { top = 0 }: ScrollToOptions) {
          this.scrollTop = top
        },
      })
    })

    afterEach(() => {
      vi.restoreAllMocks()
      Reflect.deleteProperty(Element.prototype, "scrollTo")
    })

    /** Open the thread at `path` and replay its three turns, then say where the transcript sits. */
    async function scrolledAt(path: string) {
      window.history.replaceState(null, "", path)
      const { subscription } = openThread()
      const events = thread(...turns.map((turn): [string, Payload] => [turn, started(turn)]))
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      return screen.getByRole("region", { name: "Messages" }).scrollTop
    }

    it("opens the thread scrolled to that turn, not the latest", async () => {
      expect(await scrolledAt("/instances/hub-ada/threads/t1/turns/turn-2")).toBe(1_000)
    })

    it("opens the thread on its latest turn when the thread has no such turn", async () => {
      expect(await scrolledAt("/instances/hub-ada/threads/t1/turns/turn-9")).toBe(2_000)
    })

    it("leaves a thread opened without a turn on its latest turn", async () => {
      expect(await scrolledAt("/instances/hub-ada/threads/t1")).toBe(2_000)
    })

    it("scrolls to the turn again when Back returns to it from the plain thread", async () => {
      await scrolledAt("/instances/hub-ada/threads/t1/turns/turn-2")
      const transcript = screen.getByRole("region", { name: "Messages" })

      act(() => {
        window.history.pushState(null, "", "/instances/hub-ada/threads/t1")
        window.dispatchEvent(new PopStateEvent("popstate"))
      })
      transcript.scrollTop = 0
      act(() => {
        window.history.pushState(null, "", "/instances/hub-ada/threads/t1/turns/turn-2")
        window.dispatchEvent(new PopStateEvent("popstate"))
      })

      await waitFor(() => expect(transcript.scrollTop).toBe(1_000))
    })
  })

  it("draws a reply without the instance's name beside it", async () => {
    const events = thread(
      ["turn-1", started("Say hi")],
      ["turn-1", { type: "message.delta", text: "Hi." }],
    )
    const { subscription } = openThread()
    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })

    expect(screen.getByText("Hi.")).toBeDefined()
    expect(screen.queryByText("Ada")).toBeNull()
  })

  describe("a turn that has shown nothing yet", () => {
    it("thinks until its first step", async () => {
      const events = thread(
        ["turn-1", started("Plan the trip")],
        ["turn-1", { type: "message.delta", text: "Looking." }],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(1)
        subscription().deliver(events[0] as Event)
      })
      expect(screen.getByText("Thinking")).toBeDefined()

      await act(async () => subscription().deliver(events[1] as Event))

      expect(screen.queryByText("Thinking")).toBeNull()
      expect(screen.getByText("Looking.")).toBeDefined()
    })

    it("stops thinking when the turn ends", async () => {
      const events = thread(
        ["turn-1", started("Plan the trip")],
        ["turn-1", { type: "turn.interrupted" }],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })

      expect(screen.queryByText("Thinking")).toBeNull()
    })
  })

  describe("day dividers", () => {
    beforeEach(() => {
      vi.stubEnv("TZ", "America/Bogota")
      vi.useFakeTimers({ toFake: ["Date"] })
      vi.setSystemTime(new Date("2026-09-28T15:00:00Z"))
    })

    afterEach(() => {
      vi.useRealTimers()
      vi.unstubAllEnvs()
    })

    /** Open a thread with one turn started at each of `timestamps`, asking "Ask 0", "Ask 1", … */
    async function startedAt(...timestamps: string[]) {
      const events = thread(
        ...timestamps.map((_, index): [string, Payload] => [
          `turn-${index}`,
          started(`Ask ${index}`),
        ]),
      ).map((event, index) => ({ ...event, timestamp: timestamps[index] as string }))
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
    }

    /** Each of `texts`, shown once, read top to bottom. */
    function read(texts: string[]) {
      const view = within(screen.getByRole("region", { name: "Messages" }))
      return texts
        .map((text) => view.getByText(text))
        .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))
        .map((node) => node.textContent)
    }

    it("marks each day the browser's zone places a turn on, once", async () => {
      await startedAt(
        "2026-09-25T15:00:00Z",
        // Past midnight in UTC, still the evening before in Bogotá.
        "2026-09-27T03:00:00Z",
        "2026-09-27T15:00:00Z",
        "2026-09-28T10:00:00Z",
        "2026-09-28T14:00:00Z",
      )
      const days = [
        "Fri, Sep 25, 2026",
        "Ask 0",
        "Sat, Sep 26, 2026",
        "Ask 1",
        "Yesterday",
        "Ask 2",
        "Today",
        "Ask 3",
        "Ask 4",
      ]

      expect(read(days)).toEqual(days)
    })
  })

  describe("a reply", () => {
    /** Open a thread whose one turn asked `request` and was answered with `reply`. */
    async function replied(request: string, reply: string) {
      const events = thread(
        ["turn-1", started(request)],
        ["turn-1", { type: "message.delta", text: reply }],
      )
      const { subscription } = openThread()
      await act(async () => {
        subscription().subscribed(events.length)
        for (const event of events) subscription().deliver(event)
      })
      return screen.getByRole("region", { name: "Messages" })
    }

    it("renders its Markdown", async () => {
      const reply = await replied("Explain it", "The **runtime** reads `config.toml` first.")

      expect(reply.querySelector("strong")?.textContent).toBe("runtime")
      expect(reply.querySelector("code")?.textContent).toBe("config.toml")
      expect(reply.textContent).not.toContain("**")
      expect(reply.textContent).not.toContain("`")
    })

    it("renders a reply cut off mid-emphasis without the open marker", async () => {
      const reply = await replied("Explain it", "The **runtime reads")

      expect(reply.querySelector("strong")?.textContent).toBe("runtime reads")
      expect(reply.textContent).not.toContain("**")
    })

    it("renders a reply cut off inside a code fence without the fence", async () => {
      const reply = await replied("Show it", "Run this:\n\n```sh\nmake deploy")

      expect(reply.querySelector("pre")?.textContent).toContain("make deploy")
      expect(reply.textContent).not.toContain("```")
    })

    it("highlights the syntax of a fenced code block", async () => {
      const reply = await replied("Show it", "```python\nimport os\n```")

      await waitFor(() => {
        const keyword = [...reply.querySelectorAll("pre span")].find(
          (token) => token.textContent === "import",
        )
        expect(keyword?.getAttribute("style")).toContain("--sdm-c")
      })
    })

    it("leaves the request as typed", async () => {
      await replied("Deploy **now**, not `later`", "Deploying.")

      expect(screen.getByText("Deploy **now**, not `later`")).toBeDefined()
    })

    it("shows raw HTML as text", async () => {
      const reply = await replied("Explain it", "Use <b>bold</b> sparingly.")

      expect(reply.querySelector("b")).toBeNull()
      expect(reply.textContent).toContain("Use <b>bold</b> sparingly.")
    })

    it("loads no image it links to", async () => {
      const reply = await replied("Explain it", "Done. ![pixel](https://evil.example/?q=secret)")

      expect(reply.querySelector("img")).toBeNull()
      expect(reply.textContent).toContain("Done.")
    })

    it("opens its links in a new tab", async () => {
      await replied("Where is it?", "See [the docs](https://kinby.dev/docs).")

      const link = screen.getByRole("link", { name: "the docs" })
      expect(link.getAttribute("href")).toBe("https://kinby.dev/docs")
      expect(link.getAttribute("target")).toBe("_blank")
    })

    describe("a link to a page of the app", () => {
      const page = "/instances/hub-bo/threads/t9"

      beforeEach(() => window.history.replaceState(null, "", "/instances/hub-ada/threads/t1"))

      it("opens the page in place, as a new history entry", async () => {
        await replied("Ask Bo", `Bo is on it in [the thread](${page}).`)
        const link = screen.getByRole("link", { name: "the thread" })
        const entries = window.history.length

        await userEvent.click(link)

        expect(link.getAttribute("href")).toBe(page)
        expect(link.getAttribute("target")).toBeNull()
        expect(window.location.pathname).toBe(page)
        expect(window.history.length).toBe(entries + 1)
      })

      it("leaves a modified click to the browser", async () => {
        await replied("Ask Bo", `Bo is on it in [the thread](${page}).`)
        const link = screen.getByRole("link", { name: "the thread" })

        // jsdom cannot follow the link, so the window drops the click once the app has passed it on.
        const prevented: boolean[] = []
        const drop = (event: MouseEvent) => {
          prevented.push(event.defaultPrevented)
          event.preventDefault()
        }
        window.addEventListener("click", drop)
        onTestFinished(() => window.removeEventListener("click", drop))

        fireEvent.click(link, { ctrlKey: true })

        expect(prevented).toEqual([false])
        expect(window.location.pathname).toBe("/instances/hub-ada/threads/t1")
      })
    })
  })
})
