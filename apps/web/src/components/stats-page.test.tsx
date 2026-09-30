import type { ReportedRun, StatsGetResult, StatsSummary, TurnMetrics } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { StatsPage } from "@/components/stats-page"

function summary(fields: Partial<StatsSummary> = {}): StatsSummary {
  return {
    completed: 0,
    failed: 0,
    interrupted: 0,
    input_tokens: 0,
    output_tokens: 0,
    recap_input_tokens: 0,
    recap_output_tokens: 0,
    cost: null,
    tool_calls: {},
    memory_calls: {},
    turns_without_memory: 0,
    approvals_requested: 0,
    mean_duration_seconds: null,
    good_ratings: 0,
    bad_ratings: 0,
    subscriptions: [
      { usage_source: "claude-subscription", runs: 0 },
      { usage_source: "chatgpt-subscription", runs: 0 },
    ],
    ...fields,
  }
}

function stats(fields: Partial<StatsGetResult> = {}): StatsGetResult {
  return {
    records: [],
    buckets: [],
    total: summary(),
    plan_windows: [],
    limits: [],
    unpriced_models: [],
    warnings: [],
    ...fields,
  }
}

function turn(fields: Partial<TurnMetrics> & Pick<TurnMetrics, "turn_id">): TurnMetrics {
  return {
    thread_id: "thread-chat",
    model: "openai:gpt-5",
    prompt_version: null,
    origin: { kind: "user" },
    closing_kind: "completed",
    started_at: null,
    closed_at: "2026-09-28T10:00:00Z",
    duration_seconds: null,
    input_tokens: 0,
    output_tokens: 0,
    recap_input_tokens: 0,
    recap_output_tokens: 0,
    cost: 0,
    tool_calls: {},
    memory_calls: {},
    memory_consulted: false,
    approvals_requested: 0,
    memory_tokens: 0,
    rating: null,
    ...fields,
  }
}

function delegated(client: string): ReportedRun {
  return {
    timestamp: "2026-09-28T09:59:00Z",
    run: {
      client,
      client_turns: 1,
      duration_ms: 60_000,
      input_tokens: 0,
      output_tokens: 0,
      models: [],
      outcome: "completed",
      usage_source: "claude-subscription",
    },
  }
}

/**
 * Click the bar of the bucket at `index`, as a pointer does. jsdom lays nothing out, so Recharts
 * draws no shape to find by role, only the layer each bar sits in.
 */
function clickBar(index: number) {
  const bar = document.querySelectorAll(".recharts-bar-rectangle")[index]?.firstElementChild
  if (!bar) throw new Error(`The chart has no bar ${index}`)
  fireEvent.click(bar)
}

function openStats(answers: Answers = { "stats.get": () => stats() }) {
  const client = stubCaller(answers)
  render(<StatsPage client={client} clock={fakeClock()} instanceId="hub-ada" />)
  return client
}

/** What each stats.get asked for, oldest first. */
function statsReads(client: ReturnType<typeof stubCaller>) {
  return client.calls.filter((call) => call.method === "stats.get").map((call) => call.params)
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] })
  vi.setSystemTime(new Date("2026-09-30T15:20:00Z"))
})

afterEach(() => {
  vi.useRealTimers()
})

describe("the stats page", () => {
  it("reads the last 7 days by day on open, from midnight UTC on the first one", async () => {
    const client = openStats()

    await act(() => Promise.resolve())

    expect(statsReads(client)).toEqual([{ since: "2026-09-24T00:00:00.000Z", by: "day" }])
  })

  it("reads 30 days by day and 90 days by week when the range changes", async () => {
    const client = openStats()
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "30 days" }))
    await user.click(screen.getByRole("button", { name: "90 days" }))

    expect(statsReads(client).slice(1)).toEqual([
      { since: "2026-09-01T00:00:00.000Z", by: "day" },
      { since: "2026-07-03T00:00:00.000Z", by: "week" },
    ])
  })

  it("opens on Overview, and shows Spend, Origin, and Quality as unavailable", async () => {
    openStats()

    const overview = await screen.findByRole("tab", { name: "Overview" })

    expect(overview.getAttribute("aria-selected")).toBe("true")
    for (const name of ["Spend", "Origin", "Quality"]) {
      expect(screen.getByRole("tab", { name }).getAttribute("aria-disabled")).toBe("true")
    }
  })

  it("adds up the range in tiles: turns, API cost, plan runs, and ratings", async () => {
    openStats({
      "stats.get": () =>
        stats({
          total: summary({
            completed: 5,
            failed: 1,
            interrupted: 2,
            input_tokens: 12_000,
            output_tokens: 3_400,
            cost: 1.236,
            good_ratings: 3,
            bad_ratings: 1,
            subscriptions: [
              { usage_source: "claude-subscription", runs: 3 },
              { usage_source: "chatgpt-subscription", runs: 1 },
            ],
          }),
        }),
    })

    const tile = async (name: string) => (await screen.findByRole("region", { name })).textContent

    expect(await tile("Turns")).toBe("Turns81 failed · 2 interrupted")
    expect(await tile("API cost")).toBe("API cost$1.2412,000 in · 3,400 out")
    expect(await tile("Plan runs")).toBe("Plan runs4Claude 3 · ChatGPT 1")
    expect(await tile("Ratings")).toBe("Ratings3 good1 bad")
  })

  it("shows no cost and no ratings as a dash", async () => {
    openStats()

    expect((await screen.findByRole("region", { name: "API cost" })).textContent).toContain("—")
    expect(screen.getByRole("region", { name: "Ratings" }).textContent).toContain("—")
  })

  it("names the unpriced models and the turns whose model calls don't add up", async () => {
    const mismatch = { thread_id: "t", turn_id: "u" }
    openStats({
      "stats.get": () =>
        stats({ unpriced_models: ["ollama:llama3", "mystery"], warnings: [mismatch, mismatch] }),
    })

    expect(await screen.findByText("No price for ollama:llama3 and mystery")).toBeDefined()
    expect(screen.getByText("2 turns' model calls don't add up")).toBeDefined()
  })

  it("shows no notice when every model is priced and every turn adds up", async () => {
    openStats()

    await screen.findByRole("region", { name: "Turns" })

    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("lists the turns of a clicked bar's bucket by cost, each linking to its thread", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/stats")
    openStats({
      "stats.get": () =>
        stats({
          records: [
            turn({ turn_id: "chat", cost: 0.5, rating: { type: "turn.rated", verdict: "good" } }),
            turn({
              turn_id: "inbox",
              thread_id: "thread-inbox",
              cost: 1.2,
              closing_kind: "failed",
              origin: { kind: "routine", name: "inbox", trigger: "scheduled" },
              delegated_runs: [delegated("claude-code"), delegated("codex")],
            }),
            turn({
              turn_id: "babysit",
              cost: null,
              origin: {
                kind: "routine",
                name: "babysit",
                trigger: "signal",
                delivery_id: "delivery-7",
              },
            }),
            turn({ turn_id: "unknown", cost: 0.1, origin: null }),
            turn({ turn_id: "next day", closed_at: "2026-09-29T08:00:00Z" }),
          ],
          buckets: [
            { ...summary({ completed: 3, failed: 1 }), start: "2026-09-28" },
            { ...summary({ completed: 1 }), start: "2026-09-29" },
          ],
        }),
    })
    const user = userEvent.setup()
    await screen.findByRole("region", { name: "Turns" })

    clickBar(0)

    const listed = screen.getByRole("table", { name: "Turns closed on Sep 28" })
    expect(
      within(listed)
        .getAllByRole("row")
        .slice(1)
        .map((row) =>
          within(row)
            .getAllByRole("cell")
            .map((cell) => cell.textContent),
        ),
    ).toEqual([
      ["inbox · scheduled", "failed", "—", "claude-code · codex", "$1.20", "Open in chat"],
      ["Chat", "completed", "Good", "—", "$0.50", "Open in chat"],
      ["Chat", "completed", "—", "—", "$0.10", "Open in chat"],
      ["babysit · signal", "completed", "—", "—", "—", "Open in chat"],
    ])
    await user.click(within(listed).getAllByRole("link", { name: "Open in chat" })[0])
    expect(window.location.pathname).toBe("/instances/hub-ada/threads/thread-inbox")
  })

  it("lists a week bucket's turns from its Monday through its Sunday, in UTC", async () => {
    openStats({
      "stats.get": () =>
        stats({
          records: [
            turn({ turn_id: "sunday before", closed_at: "2026-09-27T23:59:00Z" }),
            turn({ turn_id: "monday", closed_at: "2026-09-28T00:00:00Z", cost: 0.2 }),
            turn({ turn_id: "wednesday", closed_at: "2026-09-30T15:00:00+02:00", cost: 0.3 }),
          ],
          buckets: [
            { ...summary({ completed: 1 }), start: "2026-09-21" },
            { ...summary({ completed: 2 }), start: "2026-09-28" },
          ],
        }),
    })
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "90 days" }))

    clickBar(1)

    const listed = screen.getByRole("table", { name: "Turns closed in the week of Sep 28" })
    expect(within(listed).getAllByRole("row").slice(1)).toHaveLength(2)
    expect(within(listed).getByText("$0.30")).toBeDefined()
    expect(within(listed).getByText("$0.20")).toBeDefined()
  })

  it("reads again on window focus and on Refresh, and never on its own", async () => {
    const client = stubCaller({ "stats.get": () => stats() })
    const clock = fakeClock()
    render(<StatsPage client={client} clock={clock} instanceId="hub-ada" />)
    const user = userEvent.setup()
    await act(() => clock.advance(10 * 60_000))
    expect(statsReads(client)).toHaveLength(1)

    act(() => {
      window.dispatchEvent(new Event("focus"))
    })
    await user.click(screen.getByRole("button", { name: "Refresh" }))

    expect(statsReads(client)).toEqual(
      Array(3).fill({ since: "2026-09-24T00:00:00.000Z", by: "day" }),
    )
  })
})
