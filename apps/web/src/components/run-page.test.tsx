import type { FactoryRun, FactoryRunDetail, FactoryRunTranscript } from "@kinby/contract"
import { type Answers, fakeClock, stubSubscriber } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it } from "vitest"

import { attempt, detail, hub } from "@/components/factories-testing"
import { RunPage } from "@/components/run-page"

function openRun(runId: string, more: Answers = {}, clock = fakeClock()) {
  const caller = hub(more)
  const subscriber = stubSubscriber()
  render(
    <RunPage
      hub={{ call: caller.call, subscribe: subscriber.subscribe }}
      clock={clock}
      runId={runId}
    />,
  )
  return { caller, subscriber }
}

/** The run's steps, in order, each as its row reads. */
async function steps(): Promise<string[]> {
  const list = await screen.findByRole("list", { name: "Steps" })
  return within(list)
    .getAllByRole("button")
    .map((row) => row.textContent ?? "")
}

/** What the user waits on, as the card at the top of the page says it. */
const waiting = () => within(screen.getByRole("region", { name: "Waiting on you" }))

describe("a run's page", () => {
  beforeEach(() => window.history.replaceState(null, "", "/factories/runs/r-stopped"))

  it("lists the run's steps in the factory's order with each one's kind, engine, attempts and last outcome", async () => {
    openRun("r-stopped")

    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("issue 1305")
    expect(await steps()).toEqual([
      "ticketcode1 attemptClean",
      "implementagent · claude2 attemptsFailed",
      "checkscommand1 attemptFailed",
      "publishcodeNot reached",
    ])
  })

  it("opens the current step on its attempts in order, each with its session, cost, summary and transcript", async () => {
    openRun("r-stopped")

    const implement = within(await screen.findByRole("region", { name: "implement" }))
    const [first, second] = implement.getAllByRole("article")
    const one = within(first as HTMLElement)
    expect(one.getByText(/^Attempt 1 · claude-1 · .* · 20m 0s · \$0\.42$/)).toBeDefined()
    expect(one.getByText("Implemented the board.")).toBeDefined()
    expect(await one.findByText("Reading the ticket.")).toBeDefined()
    expect(one.getByText("gh issue view 1305")).toBeDefined()
    expect(within(second as HTMLElement).getByText("The board test still fails.")).toBeDefined()
    expect(screen.queryByRole("region", { name: "ticket" })).toBeNull()
  })

  it("names the factory version each attempt ran", async () => {
    const stopped = detail("r-stopped")
    const version = "3f2a9c1e".padEnd(40, "0")
    const attempts = stopped.attempts.map((ran) => ({ ...ran, version }))
    openRun("r-stopped", { "factory.run.get": () => ({ ...stopped, attempts }) })

    const implement = within(await screen.findByRole("region", { name: "implement" }))
    const [first] = implement.getAllByRole("article")
    expect(
      within(first as HTMLElement).getByText(/^Attempt 1 · version 3f2a9c1 · claude-1 · /),
    ).toBeDefined()
  })

  it("links a failed attempt to its step's editor, from the run", async () => {
    openRun("r-stopped")

    const implement = within(await screen.findByRole("region", { name: "implement" }))
    const [clean, failed] = implement.getAllByRole("article")
    expect(within(clean as HTMLElement).queryByRole("link", { name: "Edit this step" })).toBeNull()
    expect(
      within(failed as HTMLElement)
        .getByRole("link", { name: "Edit this step" })
        .getAttribute("href"),
    ).toBe("/library/factories?factory=software&step=implement&run=r-stopped")
  })

  it("shows a command step's exit and the tail of its failure", async () => {
    const user = userEvent.setup()
    openRun("r-stopped")

    await user.click(await screen.findByRole("button", { name: /^checks/ }))

    const checks = within(screen.getByRole("region", { name: "checks" }))
    expect(checks.getByText(/exited with code 1\.\s+FAIL src\/board\.test\.tsx/)).toBeDefined()
  })

  it("reads the run again while it moves", async () => {
    const clock = fakeClock()
    let run: FactoryRun = detail("r-running").run
    openRun("r-running", { "factory.run.get": () => ({ ...detail("r-running"), run }) }, clock)
    await screen.findByText("Running at implement")

    run = { ...run, status: "done", step: null }
    await clock.advance(5_000)

    expect(await screen.findByText("Done")).toBeDefined()
  })

  it("reads an attempt's transcript once more when the attempt ends", async () => {
    const clock = fakeClock()
    const said = (text: string) => ({
      type: "assistant",
      message: { content: [{ type: "text", text }] },
    })
    let current = detail("r-running")
    let events = [said("Working.")]
    let now = 0
    const pass = async (ms: number) => {
      now += ms
      await clock.advance(ms)
    }
    // Time passes until the text shows, however late the page asks for it.
    const shows = (text: string) =>
      waitFor(async () => {
        await pass(250)
        expect(screen.getByText(text)).toBeDefined()
      })
    // A transcript comes from the workbench, so it lands a second after the run it belongs to.
    const transcript = () =>
      new Promise<FactoryRunTranscript>((resolve) =>
        clock.after(1_000, () => resolve({ events, cursor: null })),
      )
    openRun(
      "r-running",
      {
        "factory.run.get": () => current,
        "factory.run.transcript": transcript as unknown as Answers["factory.run.transcript"],
      },
      clock,
    )
    await screen.findByRole("region", { name: "implement" })
    await shows("Working.")

    const [running] = current.attempts
    current = {
      ...current,
      attempts: [{ ...running!, ended_at: "2026-10-12T09:30:00Z", ending: "clean" }],
    }
    events = [said("Working."), said("Opened the pull request.")]
    // The run's next read falls due at 5 s, before the transcript's.
    await pass(5_000 - now)
    await screen.findByText("Clean")
    // The page has seen the attempt end before its next transcript read falls due.
    await act(async () => {})

    await shows("Opened the pull request.")
  })

  it("retries a run that needs a human", async () => {
    const user = userEvent.setup()
    const { caller } = openRun("r-stopped", { "factory.run.retry": () => detail("r-stopped").run })

    await screen.findByRole("region", { name: "Waiting on you" })
    expect(waiting().getByText("Needs a human at implement")).toBeDefined()
    await user.click(waiting().getByRole("button", { name: "Retry implement" }))

    await waitFor(() =>
      expect(caller.calls).toContainEqual({
        method: "factory.run.retry",
        params: { run_id: "r-stopped", latest: false },
      }),
    )
  })

  it("retries a run that needs a human on the factory's latest version", async () => {
    const user = userEvent.setup()
    const { caller } = openRun("r-stopped", { "factory.run.retry": () => detail("r-stopped").run })

    await screen.findByRole("region", { name: "Waiting on you" })
    await user.click(waiting().getByRole("switch", { name: "On the factory's latest version" }))
    await user.click(waiting().getByRole("button", { name: "Retry implement" }))

    await waitFor(() =>
      expect(caller.calls).toContainEqual({
        method: "factory.run.retry",
        params: { run_id: "r-stopped", latest: true },
      }),
    )
  })

  it("sends a run that needs a human back to an earlier step, with notes", async () => {
    const user = userEvent.setup()
    const { caller } = openRun("r-stopped", {
      "factory.run.send_back": () => detail("r-stopped").run,
    })

    await screen.findByRole("region", { name: "Waiting on you" })
    await user.click(waiting().getByRole("button", { name: "Send back…" }))
    const dialog = within(await screen.findByRole("dialog"))
    expect(dialog.getByRole("combobox", { name: "Step" }).textContent).toContain("ticket")
    await user.type(dialog.getByRole("textbox", { name: "Notes" }), "Read the parent issue.")
    await user.click(dialog.getByRole("button", { name: "Send back" }))

    await waitFor(() =>
      expect(caller.calls).toContainEqual({
        method: "factory.run.send_back",
        params: {
          run_id: "r-stopped",
          step: "ticket",
          notes: "Read the parent issue.",
          latest: false,
        },
      }),
    )
  })

  it("cancels a run that needs a human once the user confirms", async () => {
    const user = userEvent.setup()
    const { caller } = openRun("r-stopped", { "factory.run.cancel": () => detail("r-stopped").run })

    await screen.findByRole("region", { name: "Waiting on you" })
    await user.click(waiting().getByRole("button", { name: "Cancel run" }))
    await user.click(await screen.findByRole("button", { name: "Cancel the run" }))

    await waitFor(() =>
      expect(caller.calls).toContainEqual({
        method: "factory.run.cancel",
        params: { run_id: "r-stopped" },
      }),
    )
  })

  it("approves a run waiting at an approve step", async () => {
    const user = userEvent.setup()
    const { caller } = openRun("r-approve", {
      "factory.run.approve": () => detail("r-approve").run,
    })

    await screen.findByRole("region", { name: "Waiting on you" })
    expect(waiting().getByText("review waits for your approval")).toBeDefined()
    expect(waiting().getByRole("button", { name: "Send back…" })).toBeDefined()
    await user.click(waiting().getByRole("button", { name: "Approve" }))

    await waitFor(() =>
      expect(caller.calls).toContainEqual({
        method: "factory.run.approve",
        params: { run_id: "r-approve" },
      }),
    )
  })

  it.each([
    ["Approve", "approve"],
    ["Deny", "deny"],
  ] as const)("answers an agent step's gate ask: %s", async (button, decision) => {
    const user = userEvent.setup()
    const asking: FactoryRunDetail = {
      ...detail("r-asking"),
      attempts: [
        attempt({
          ending: "asked",
          ask: {
            turn_id: "turn-2",
            request: {
              type: "approval.requested",
              approval_id: "ap-1",
              name: "write",
              arguments: { path: "draft.md" },
              rule: "mode.ask.write",
            },
          },
        }),
      ],
    }
    const { caller } = openRun("r-asking", {
      "factory.run.get": () => asking,
      "factory.run.answer": () => asking.run,
    })

    await screen.findByRole("region", { name: "Waiting on you" })
    expect(waiting().getByText("implement asks to call write")).toBeDefined()
    expect(waiting().getByText("draft.md")).toBeDefined()
    await user.click(waiting().getByRole("button", { name: button }))

    await waitFor(() =>
      expect(caller.calls).toContainEqual({
        method: "factory.run.answer",
        params: { run_id: "r-asking", approval_id: "ap-1", answer: { decision } },
      }),
    )
  })

  it("opens the takeover an agent step asks for on its workbench, and finishes it", async () => {
    const user = userEvent.setup()
    const { caller, subscriber } = openRun("r-asking", {
      "factory.run.takeover.finish": () => detail("r-asking").run,
    })

    await screen.findByRole("region", { name: "Waiting on you" })
    expect(waiting().getByText("implement needs you to take over the browser")).toBeDefined()
    expect(waiting().getByText("Sign in.")).toBeDefined()
    await user.click(waiting().getByRole("button", { name: "Take over" }))

    expect(subscriber.subscriptions.map(({ method, params }) => ({ method, params }))).toEqual([
      {
        method: "thread.takeover.start",
        params: { instance_id: "wb-landing", turn_id: "turn-1" },
      },
    ])
    await user.click(waiting().getByRole("button", { name: "Finish" }))

    await waitFor(() =>
      expect(caller.calls).toContainEqual({
        method: "factory.run.takeover.finish",
        params: { run_id: "r-asking", takeover_id: "t-1" },
      }),
    )
  })

  it("shows nothing waiting on the user while the run runs", async () => {
    openRun("r-running")

    await screen.findByText("Running at implement")
    expect(screen.queryByRole("region", { name: "Waiting on you" })).toBeNull()
  })
})
