import { CallError, type Event, type PermissionMode, type SkillSummary } from "@kinby/contract"
import { type Answers, fakeClock } from "@kinby/contract/testing"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ThreadComposer } from "@/components/thread-composer"
import {
  instanceClient,
  openThread,
  started,
  thread,
  threadSummary,
} from "@/components/thread-testing"

// jsdom lays nothing out, so it has no scrollIntoView, and the skill menu calls it on its rows.
Element.prototype.scrollIntoView = () => {}

/** How the instance lists thread t1, as `listed()` returns it, under `ceiling`. */
function listing(listed = () => threadSummary(), ceiling: PermissionMode = "full-access") {
  return () => ({ threads: [listed()], ceiling, cursor: null })
}

const modePicker = () => screen.findByRole("combobox", { name: "Mode" })

/** Open the composer of `threadId`, from a client that answers from `answers`. */
const open = (
  threadId: string,
  answers: Answers = {},
  client = instanceClient({ "thread.list": listing(), ...answers }),
) => ({
  client,
  ...render(
    <ThreadComposer
      client={client}
      clock={fakeClock()}
      threadId={threadId}
      name="Ada"
      running={false}
      contextSize={undefined}
      modelWindow={undefined}
    />,
  ),
})
const box = () => screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message Ada" })

describe("a thread's panel", () => {
  it("puts focus in the composer once the replay has loaded", async () => {
    const { subscription } = openThread()

    await act(async () => subscription().subscribed(0))

    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Message Ada" }))
  })

  it("starts a turn with the message when Enter is pressed", async () => {
    const { sent, subscription } = openThread({
      "thread.turn.start": () => ({ sequence: 1, thread_id: "t1", turn_id: "turn-1" }),
    })
    await act(async () => subscription().subscribed(0))
    const user = userEvent.setup()

    await user.type(screen.getByRole("textbox", { name: "Message Ada" }), "Fix the runtime{Enter}")

    expect(sent()).toEqual([
      {
        method: "thread.turn.start",
        params: { thread_id: "t1", message: "Fix the runtime", skills: [], uploads: [] },
      },
    ])
    expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message Ada" }).value).toBe("")
  })

  it("turns Send into Stop while a turn runs, and Stop interrupts the turn", async () => {
    const events = thread(
      ["turn-1", started("Deploy it")],
      ["turn-1", { type: "turn.interrupted" }],
    )
    const { sent, subscription } = openThread({
      "thread.turn.interrupt": () => ({ sequence: 2, thread_id: "t1", turn_id: "turn-1" }),
    })
    await act(async () => {
      subscription().subscribed(0)
      subscription().deliver(events[0] as Event)
    })
    const user = userEvent.setup()

    expect(screen.queryByRole("button", { name: "Send" })).toBeNull()
    await user.click(screen.getByRole("button", { name: "Stop" }))
    expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])

    await act(async () => subscription().deliver(events[1] as Event))
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull()
    expect(screen.getByRole("button", { name: "Send" })).toBeDefined()
  })

  it("interrupts the running turn on Escape, unless Escape cancels an IME composition", async () => {
    const { sent, subscription } = openThread({
      "thread.turn.interrupt": () => ({ sequence: 2, thread_id: "t1", turn_id: "turn-1" }),
    })
    await act(async () => {
      subscription().subscribed(0)
      subscription().deliver(thread(["turn-1", started("Deploy it")])[0] as Event)
    })
    const field = screen.getByRole("textbox", { name: "Message Ada" })

    fireEvent.keyDown(field, { key: "Escape", isComposing: true })
    expect(sent()).toEqual([])

    fireEvent.keyDown(field, { key: "Escape" })
    expect(sent()).toEqual([{ method: "thread.turn.interrupt", params: { thread_id: "t1" } }])
  })

  it("says under the composer that the instance can make mistakes", async () => {
    const { subscription } = openThread()

    await act(async () => subscription().subscribed(0))

    expect(
      screen.getByText("Ada can make mistakes. It asks before anything it can't undo."),
    ).toBeDefined()
  })

  it("compacts the thread from the bar with the line of focus given", async () => {
    const events = thread(
      ["turn-1", started("Fix the runtime")],
      [
        "turn-1",
        {
          type: "model.completed",
          model: "claude",
          input_tokens: 41_000,
          output_tokens: 50,
          duration_ms: 900,
        },
      ],
      ["turn-1", { type: "turn.completed", input_tokens: 41_000, output_tokens: 50 }],
    )
    const { client, subscription } = openThread({
      "thread.context.get": () => ({ context_size: 41_000, rows: [] }),
      "thread.compact": () => ({ sequence: 4, thread_id: "t1", turn_id: "turn-1" }),
    })
    await act(async () => {
      subscription().subscribed(events.length)
      for (const event of events) subscription().deliver(event)
    })
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "What fills the context" }))
    await user.type(await screen.findByRole("textbox", { name: "Focus" }), "the deploy")
    await user.click(screen.getByRole("button", { name: "Compact" }))

    expect(client.calls.filter((call) => call.method === "thread.compact")).toEqual([
      { method: "thread.compact", params: { thread_id: "t1", focus: "the deploy" } },
    ])
  })

  describe("the mode", () => {
    it("shows the thread's mode and disables the modes above the instance's ceiling", async () => {
      const { subscription } = openThread({ "thread.list": listing(undefined, "ask") })
      await act(async () => subscription().subscribed(0))
      const user = userEvent.setup()

      expect((await modePicker()).textContent).toContain("Ask")
      await user.click(await modePicker())
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
        modes.map((mode) => mode.textContent?.includes("Above the ceiling. Raise it in Config.")),
      ).toEqual([false, false, true, true])
    })

    it("pins the mode picked and shows it once the instance lists it", async () => {
      let mode: PermissionMode = "ask"
      const { sent, subscription } = openThread({
        "thread.list": listing(() => threadSummary({ mode, mode_pinned: mode !== "ask" })),
        "thread.mode.set": (params) => {
          mode = params.mode
          return { thread_id: "t1", turn_id: "pin", sequence: 1 }
        },
      })
      await act(async () => subscription().subscribed(0))
      const user = userEvent.setup()

      await user.click(await modePicker())
      await user.click(await screen.findByRole("option", { name: /^Read-only/ }))

      expect(sent()).toEqual([
        { method: "thread.mode.set", params: { thread_id: "t1", mode: "read-only" } },
      ])
      expect((await modePicker()).textContent).toContain("Read-only")
    })

    it("refuses another pick while the change is out, and shows why once it fails", async () => {
      const lister = instanceClient({ "thread.list": listing() })
      const sent: string[] = []
      let fail = (_error: CallError) => {}
      const client: typeof lister = {
        ...lister,
        call: (method, params) => {
          if (method === "thread.list" || method === "usage.get") return lister.call(method, params)
          sent.push(method)
          return new Promise<never>((_resolve, reject) => (fail = reject))
        },
      }
      const { subscription } = openThread({}, client)
      await act(async () => subscription().subscribed(0))
      const picker = await modePicker()
      const user = userEvent.setup()

      act(() => picker.focus())
      await user.keyboard("{ArrowDown}")
      await screen.findAllByRole("option")
      await user.keyboard("{ArrowUp}{Enter}")

      expect(sent).toEqual(["thread.mode.set"])
      expect(picker.getAttribute("aria-readonly")).toBe("true")
      expect(screen.getByRole("status", { name: "Changing the mode" })).toBeDefined()

      await user.click(picker)
      await user.click(await screen.findByRole("option", { name: /^Full access/ }))

      expect(sent).toEqual(["thread.mode.set"])

      await act(async () =>
        fail(
          new CallError({
            code: "CONNECTION_LOST",
            message: "The connection to the instance dropped.",
            retryable: true,
          }),
        ),
      )

      expect(screen.getByRole("alert").textContent).toContain(
        "The connection to the instance dropped.",
      )
      expect(screen.queryByRole("status", { name: "Changing the mode" })).toBeNull()
      expect(picker.hasAttribute("aria-readonly")).toBe(false)
      expect(picker.textContent).toContain("Ask")
    })
  })

  describe("the draft", () => {
    it("keeps an unsent message once the thread closes and opens again", async () => {
      const { unmount } = open("t1")
      await userEvent.setup().type(box(), "Fix the")

      unmount()
      open("t1")

      expect(box().value).toBe("Fix the")
    })

    it("keeps each thread's message apart", async () => {
      const user = userEvent.setup()
      const first = open("t1")
      await user.type(box(), "Fix the")
      first.unmount()

      const second = open("t2")
      expect(box().value).toBe("")
      await user.type(box(), "Deploy it")
      second.unmount()
      open("t1")

      expect(box().value).toBe("Fix the")
    })

    it("opens empty once the message is sent", async () => {
      const { unmount } = open("t1", {
        "thread.turn.start": () => ({ sequence: 1, thread_id: "t1", turn_id: "turn-1" }),
      })
      await userEvent.setup().type(box(), "Fix the runtime{Enter}")

      unmount()
      open("t1")

      expect(box().value).toBe("")
    })

    it("keeps what was typed after the thread opened again while the send was out", async () => {
      let land = () => {}
      const landed = new Promise<void>((resolve) => (land = resolve))
      const answering = instanceClient({
        "thread.list": listing(),
        "thread.turn.start": () => ({ sequence: 1, thread_id: "t1", turn_id: "turn-1" }),
      })
      const { unmount } = open(
        "t1",
        {},
        {
          ...answering,
          call: async (method, params) => {
            if (method === "thread.turn.start") await landed
            return answering.call(method, params)
          },
        },
      )
      const user = userEvent.setup()
      await user.type(box(), "Fix the{Enter}")
      unmount()
      const reopened = open("t1")
      await user.type(box(), " runtime")

      await act(async () => land())
      reopened.unmount()
      open("t1")

      expect(box().value).toBe("Fix the runtime")
    })

    it("keeps the message when the send fails", async () => {
      const { unmount } = open("t1")
      await userEvent.setup().type(box(), "Fix the runtime{Enter}")
      expect((await screen.findByRole("alert")).textContent).toContain("thread.turn.start")

      unmount()
      open("t1")

      expect(box().value).toBe("Fix the runtime")
    })
  })
})

describe("a skill reference", () => {
  /** The instance's skills: `planning` hides a lower tier's copy. */
  const skills: SkillSummary[] = [
    { name: "deploy", description: "Ship the app.", tier: "instance", shadowed_by: null },
    { name: "planning", description: "Plan the work.", tier: "instance", shadowed_by: null },
    { name: "planning", description: "Kinby's plan.", tier: "kinby", shadowed_by: "instance" },
    { name: "tdd", description: "Test first.", tier: "kinby", shadowed_by: null },
  ]
  const accepted = () => ({ sequence: 1, thread_id: "t1", turn_id: "turn-1" })
  const openWithSkills = (answers: Answers = {}) =>
    open("t1", { "skill.list": () => ({ skills, warnings: [] }), ...answers })
  /** The rows of the menu, by their text. */
  const rows = () => screen.queryAllByRole("option").map((row) => row.textContent)
  /** The text of each reference the composer marks, in order. */
  const marked = () => [...document.querySelectorAll("mark")].map((mark) => mark.textContent)
  const turnStarts = (client: { calls: { method: string; params: unknown }[] }) =>
    client.calls.filter((call) => call.method === "thread.turn.start").map((call) => call.params)

  it("lists the skills a turn can load once a slash starts a word, and narrows them by name", async () => {
    openWithSkills()
    const user = userEvent.setup()

    await user.type(box(), "Use /")
    expect(await screen.findAllByRole("option")).toHaveLength(3)
    expect(rows()).toEqual(["/deployShip the app.", "/planningPlan the work.", "/tddTest first."])

    await user.type(box(), "d")
    expect(rows()).toEqual(["/deployShip the app.", "/tddTest first."])

    await user.type(box(), "e")
    expect(rows()).toEqual(["/deployShip the app."])
  })

  it("lists the names that start with what was typed first", async () => {
    openWithSkills()

    await userEvent.setup().type(box(), "/p")

    await screen.findAllByRole("option")
    expect(rows()).toEqual(["/planningPlan the work.", "/deployShip the app."])
  })

  it("opens no menu for a slash inside a word, such as a URL", async () => {
    const { client } = openWithSkills()

    await userEvent.setup().type(box(), "See https://d")

    expect(rows()).toEqual([])
    expect(client.calls.map((call) => call.method)).not.toContain("skill.list")
  })

  it("puts a picked skill in the message as a reference, and sends its name with it", async () => {
    const { client } = openWithSkills({ "thread.turn.start": accepted })
    const user = userEvent.setup()

    await user.type(box(), "Use /pl")
    await screen.findAllByRole("option")
    await user.keyboard("{Enter}")

    expect(box().value).toBe("Use /planning ")
    expect(rows()).toEqual([])
    expect(marked()).toEqual(["/planning"])
    expect(turnStarts(client)).toEqual([])

    await user.type(box(), "for it{Enter}")
    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "Use /planning for it", skills: ["planning"], uploads: [] },
    ])
  })

  it("holds several references, picked with the arrow keys, Tab or a click", async () => {
    const { client } = openWithSkills({ "thread.turn.start": accepted })
    const user = userEvent.setup()

    await user.type(box(), "/")
    await screen.findAllByRole("option")
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowUp}{Tab}then /")
    await user.click(await screen.findByRole("option", { name: /^\/tdd/ }))
    expect(marked()).toEqual(["/planning", "/tdd"])
    await user.type(box(), "{Enter}")

    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "/planning then /tdd", skills: ["planning", "tdd"], uploads: [] },
    ])
  })

  it("drops a reference whose token is edited", async () => {
    const { client } = openWithSkills({ "thread.turn.start": accepted })
    const user = userEvent.setup()

    await user.type(box(), "/tdd")
    await screen.findAllByRole("option")
    await user.keyboard("{Enter}{Backspace}{Backspace}d now{Enter}")

    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "/tdd now", skills: [], uploads: [] },
    ])
  })

  it("sends a slash typed without a pick as plain text, even when it names a skill", async () => {
    const { client } = openWithSkills({ "thread.turn.start": accepted })
    const user = userEvent.setup()

    await user.type(box(), "/tdd")
    await screen.findAllByRole("option")
    await user.keyboard("{Escape}")
    expect(rows()).toEqual([])
    await user.type(box(), " and /etc/hosts{Enter}")

    expect(marked()).toEqual([])
    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "/tdd and /etc/hosts", skills: [], uploads: [] },
    ])
  })

  it("opens the menu again in the next message after one that closed it", async () => {
    openWithSkills({ "thread.turn.start": accepted })
    const user = userEvent.setup()

    await user.type(box(), "/tdd")
    await screen.findAllByRole("option")
    await user.keyboard("{Escape}{Enter}/")

    expect(box().value).toBe("/")
    expect(await screen.findAllByRole("option")).toHaveLength(3)
  })

  it("keeps the message when the instance refuses a skill it no longer has", async () => {
    openWithSkills({
      "thread.turn.start": () => {
        throw new CallError({
          code: "NOT_FOUND",
          message: 'Skill "tdd" is not available in this turn.',
          retryable: false,
        })
      },
    })
    const user = userEvent.setup()

    await user.type(box(), "/tdd")
    await screen.findAllByRole("option")
    await user.keyboard("{Enter}now{Enter}")

    expect((await screen.findByRole("alert")).textContent).toContain(
      'Skill "tdd" is not available in this turn.',
    )
    expect(box().value).toBe("/tdd now")
    expect(marked()).toEqual(["/tdd"])
  })

  it("keeps the references of a draft once the thread opens again", async () => {
    const { unmount } = openWithSkills()
    const user = userEvent.setup()
    await user.type(box(), "/tdd")
    await screen.findAllByRole("option")
    await user.keyboard("{Enter}now")

    unmount()
    const { client } = openWithSkills({ "thread.turn.start": accepted })
    expect(marked()).toEqual(["/tdd"])
    await user.type(box(), "{Enter}")

    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "/tdd now", skills: ["tdd"], uploads: [] },
    ])
  })
})

describe("files", () => {
  const accepted = () => ({ sequence: 1, thread_id: "t1", turn_id: "turn-1" })
  const uploaded = (params: { name: string }) => ({ path: `uploads/2026-11-22/${params.name}` })
  const csv = () => new File(["date,amount\n"], "march.csv", { type: "text/csv" })
  const picker = () => screen.getByLabelText<HTMLInputElement>("Files")
  const chip = (name: string) => screen.getByRole("listitem", { name })
  const send = () => screen.getByRole("button", { name: "Send" })
  const turnStarts = (client: { calls: { method: string; params: unknown }[] }) =>
    client.calls.filter((call) => call.method === "thread.turn.start").map((call) => call.params)

  it("uploads a picked file at once, as its name and base64 content, and shows it as a chip", async () => {
    const { client } = open("t1", { "upload.put": uploaded })

    await userEvent.setup().upload(picker(), csv())

    await waitFor(() =>
      expect(screen.queryByRole("status", { name: "Uploading march.csv" })).toBeNull(),
    )
    expect(client.calls.filter((call) => call.method === "upload.put")).toEqual([
      { method: "upload.put", params: { name: "march.csv", content: btoa("date,amount\n") } },
    ])
    expect(chip("march.csv").textContent).toContain("12 B")
  })

  it("uploads each file dropped on the composer", async () => {
    const { client } = open("t1", { "upload.put": uploaded })
    const pdf = new File(["%PDF"], "april.pdf")

    await act(async () => fireEvent.drop(box(), { dataTransfer: { files: [csv(), pdf] } }))

    await waitFor(() => expect(screen.queryAllByRole("status")).toEqual([]))
    expect(
      client.calls.filter((call) => call.method === "upload.put").map((call) => call.params),
    ).toEqual([
      { name: "march.csv", content: btoa("date,amount\n") },
      { name: "april.pdf", content: btoa("%PDF") },
    ])
    expect(chip("march.csv")).toBeDefined()
    expect(chip("april.pdf")).toBeDefined()
  })

  it("refuses a file over 10 MB before any call, and says why", async () => {
    const { client } = open("t1", { "upload.put": uploaded })
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "statements.zip")

    await userEvent.setup().upload(picker(), big)

    expect(client.calls.map((call) => call.method)).not.toContain("upload.put")
    expect(chip("statements.zip").textContent).toContain("Over 10 MB, so it can't be sent.")
  })

  it("shows the upload in progress, and sends only once every upload is done", async () => {
    let land = () => {}
    const landed = new Promise<void>((resolve) => (land = resolve))
    const answering = instanceClient({
      "thread.list": listing(),
      "upload.put": uploaded,
      "thread.turn.start": accepted,
    })
    const client: typeof answering = {
      ...answering,
      call: async (method, params) => {
        if (method === "upload.put") await landed
        return answering.call(method, params)
      },
    }
    open("t1", {}, client)
    const user = userEvent.setup()

    await user.upload(picker(), csv())
    await user.type(box(), "Total it{Enter}")

    expect(screen.getByRole("status", { name: "Uploading march.csv" })).toBeDefined()
    expect(send().getAttribute("aria-disabled")).toBe("true")
    expect(turnStarts(answering)).toEqual([])

    await act(async () => land())
    await user.click(send())

    expect(screen.queryByRole("status", { name: "Uploading march.csv" })).toBeNull()
    expect(turnStarts(answering)).toEqual([
      {
        thread_id: "t1",
        message: "Total it",
        skills: [],
        uploads: ["uploads/2026-11-22/march.csv"],
      },
    ])
    expect(screen.queryByRole("listitem", { name: "march.csv" })).toBeNull()
  })

  it("keeps a thread's files apart with its draft, and settles one uploaded while it was closed", async () => {
    let land = () => {}
    const landed = new Promise<void>((resolve) => (land = resolve))
    const answering = instanceClient({
      "thread.list": listing(),
      "upload.put": uploaded,
      "thread.turn.start": accepted,
    })
    const client: typeof answering = {
      ...answering,
      call: async (method, params) => {
        if (method === "upload.put") await landed
        return answering.call(method, params)
      },
    }
    const user = userEvent.setup()
    const first = open("t1", {}, client)
    await user.upload(picker(), csv())
    first.unmount()

    const second = open("t2", {}, client)
    expect(screen.queryByRole("listitem", { name: "march.csv" })).toBeNull()
    second.unmount()
    await act(async () => land())
    open("t1", {}, client)

    expect(chip("march.csv")).toBeDefined()
    expect(screen.queryByRole("status", { name: "Uploading march.csv" })).toBeNull()
    await user.click(send())
    expect(turnStarts(answering)).toEqual([
      { thread_id: "t1", message: "", skills: [], uploads: ["uploads/2026-11-22/march.csv"] },
    ])
  })

  it("starts a turn with the files and no message", async () => {
    const { client } = open("t1", { "upload.put": uploaded, "thread.turn.start": accepted })
    const user = userEvent.setup()

    await user.upload(picker(), csv())
    await user.click(send())

    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "", skills: [], uploads: ["uploads/2026-11-22/march.csv"] },
    ])
  })

  it("leaves a removed file out of the turn", async () => {
    const { client } = open("t1", { "upload.put": uploaded, "thread.turn.start": accepted })
    const user = userEvent.setup()

    await user.upload(picker(), csv())
    await user.click(screen.getByRole("button", { name: "Remove march.csv" }))
    await user.type(box(), "Total it{Enter}")

    expect(screen.queryByRole("listitem", { name: "march.csv" })).toBeNull()
    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "Total it", skills: [], uploads: [] },
    ])
  })

  it("says why an upload failed, and holds the message until the file is removed", async () => {
    const { client } = open("t1", {
      "upload.put": () => {
        throw new CallError({
          code: "CONNECTION_LOST",
          message: "The connection to the instance dropped.",
          retryable: true,
        })
      },
      "thread.turn.start": accepted,
    })
    const user = userEvent.setup()

    await user.upload(picker(), csv())
    await user.type(box(), "Total it{Enter}")

    expect(chip("march.csv").textContent).toContain("The connection to the instance dropped.")
    expect(send().getAttribute("aria-disabled")).toBe("true")
    expect(turnStarts(client)).toEqual([])

    await user.click(screen.getByRole("button", { name: "Remove march.csv" }))
    await user.click(send())

    expect(turnStarts(client)).toEqual([
      { thread_id: "t1", message: "Total it", skills: [], uploads: [] },
    ])
  })
})
