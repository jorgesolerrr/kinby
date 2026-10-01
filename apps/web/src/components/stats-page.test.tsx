import {
  CallError,
  type OriginUse,
  type ReportedRun,
  type RoutineSummary,
  type StatsGetResult,
  type StatsSummary,
  type TurnMetrics,
} from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { StatsPage } from "@/components/stats-page"
import { axisTicks, bucketTooltips, legend, rows } from "@/components/stats-testing"

function originUse(routine: string | null, fields: Partial<OriginUse> = {}): OriginUse {
  return {
    origin: routine === null ? "user" : "routine",
    routine,
    turns: 0,
    no_work: 0,
    failed: 0,
    cost: null,
    runs: [
      { usage_source: "claude-subscription", runs: 0 },
      { usage_source: "chatgpt-subscription", runs: 0 },
    ],
    ...fields,
  }
}

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
    origins: [originUse(null)],
    ...fields,
  }
}

function stats(fields: Partial<StatsGetResult> = {}): StatsGetResult {
  return {
    records: [],
    buckets: [],
    total: summary(),
    plan_use: [],
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

const TURN_SERIES = ["completed", "failed", "interrupted"] as const

/**
 * Click the `index`th drawn segment of `series` in the turns chart, as a pointer does. jsdom lays
 * nothing out, so Recharts draws no shape to find by role, only the layer each segment sits in.
 * Recharts draws no segment for a zero count, so `index` skips the buckets where `series` is 0.
 */
function clickBar(index: number, series: (typeof TURN_SERIES)[number] = "completed") {
  const layer = document.querySelectorAll(".recharts-bar")[TURN_SERIES.indexOf(series)]
  const bar = layer?.querySelectorAll(".recharts-bar-rectangle")[index]?.firstElementChild
  if (!bar) throw new Error(`The chart has no ${series} segment ${index}`)
  fireEvent.click(bar)
}

/** Each count a region lists, as its name and its value. */
function counts(region: HTMLElement): [string | null, string | null][] {
  const values = within(region).getAllByRole("definition")
  return within(region)
    .getAllByRole("term")
    .map((term, index) => [term.textContent, values[index].textContent])
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

  it("drops the last range's stats when the range changes, even if the new read fails", async () => {
    let reads = 0
    openStats({
      "stats.get": () => {
        reads += 1
        if (reads === 1) return stats()
        throw new CallError({ code: "INTERNAL", message: "The read failed.", retryable: false })
      },
    })
    const user = userEvent.setup()
    expect(await screen.findByRole("region", { name: "Turns" })).toBeTruthy()

    await user.click(screen.getByRole("button", { name: "30 days" }))

    expect(await screen.findByRole("alert")).toBeTruthy()
    expect(screen.queryByRole("region", { name: "Turns" })).toBeNull()
  })

  it("shows each plan's recent runs under the title from the one stats.get it reads", async () => {
    const client = openStats({
      "stats.get": () =>
        stats({
          plan_use: [
            { usage_source: "claude-subscription", duration_seconds: 18_000, runs: 2 },
            { usage_source: "claude-subscription", duration_seconds: 604_800, runs: 9 },
            { usage_source: "chatgpt-subscription", duration_seconds: 18_000, runs: 0 },
            { usage_source: "chatgpt-subscription", duration_seconds: 604_800, runs: 3 },
          ],
        }),
    })

    expect(await screen.findByText("Claude runs: 2 in 5h · 9 in 7d")).toBeDefined()
    expect(screen.getByText("ChatGPT runs: 0 in 5h · 3 in 7d")).toBeDefined()
    expect(client.calls.map((call) => call.method)).toEqual(["stats.get"])
  })

  it("opens on Overview, with every other tab available", async () => {
    openStats()

    const overview = await screen.findByRole("tab", { name: "Overview" })

    expect(overview.getAttribute("aria-selected")).toBe("true")
    for (const name of ["Spend", "Origin", "Quality"]) {
      expect(screen.getByRole("tab", { name }).getAttribute("aria-disabled")).toBe("false")
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

  it("shows an unpriced cost as not priced and no ratings as a dash", async () => {
    openStats()

    expect((await screen.findByRole("region", { name: "API cost" })).textContent).toContain(
      "not priced",
    )
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

  it("charts every day of the range, with no turns on the days that had none", async () => {
    openStats({
      "stats.get": () =>
        stats({
          buckets: [
            { ...summary({ completed: 2, failed: 1 }), start: "2026-09-25" },
            { ...summary({ completed: 1, interrupted: 1 }), start: "2026-09-28" },
          ],
        }),
    })
    await screen.findByRole("region", { name: "Turns" })

    expect(bucketTooltips(screen.getByRole("tabpanel"), 7)).toEqual([
      "Sep 24Completed0Failed0Interrupted0",
      "Sep 25Completed2Failed1Interrupted0",
      "Sep 26Completed0Failed0Interrupted0",
      "Sep 27Completed0Failed0Interrupted0",
      "Sep 28Completed1Failed0Interrupted1",
      "Sep 29Completed0Failed0Interrupted0",
      "Sep 30Completed0Failed0Interrupted0",
    ])
  })

  it("keeps charting the days it read when a refresh after midnight UTC fails", async () => {
    let reads = 0
    openStats({
      "stats.get": () => {
        reads += 1
        if (reads === 1) return stats()
        throw new CallError({ code: "INTERNAL", message: "The read failed.", retryable: false })
      },
    })
    const user = userEvent.setup()
    await screen.findByRole("region", { name: "Turns" })

    vi.setSystemTime(new Date("2026-10-01T00:05:00Z"))
    await user.click(screen.getByRole("button", { name: "Refresh" }))

    expect(await screen.findByRole("alert")).toBeTruthy()
    const days = bucketTooltips(screen.getByRole("tabpanel"), 7).map((tip) => tip.slice(0, 6))
    expect(days).toEqual(["Sep 24", "Sep 25", "Sep 26", "Sep 27", "Sep 28", "Sep 29", "Sep 30"])
  })

  it("says so when no turn closed in the range", async () => {
    openStats()

    expect(await screen.findByText("No turn closed in this range.")).toBeDefined()
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
      ["babysit · signal", "completed", "—", "—", "not priced", "Open in chat"],
    ])
    await user.click(within(listed).getAllByRole("link", { name: "Open in chat" })[0])
    expect(window.location.pathname).toBe("/instances/hub-ada/threads/thread-inbox")
  })

  it("labels a drilled turn that found no work apart from a completed one", async () => {
    openStats({
      "stats.get": () =>
        stats({
          records: [
            turn({ turn_id: "chat", cost: 0.5 }),
            turn({
              turn_id: "babysit",
              outcome: "no-work",
              origin: { kind: "routine", name: "babysit", trigger: "scheduled" },
            }),
          ],
          buckets: [{ ...summary({ completed: 2 }), start: "2026-09-28" }],
        }),
    })
    await screen.findByRole("region", { name: "Turns" })

    clickBar(0)

    const listed = screen.getByRole("table", { name: "Turns closed on Sep 28" })
    const [completed, noWork] = within(listed)
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[1].firstElementChild)
    expect(completed?.textContent).toBe("completed")
    expect(noWork?.textContent).toBe("no work")
    expect(noWork?.className).not.toBe(completed?.className)
  })

  it.each(["failed", "interrupted"] as const)(
    "lists the turns of the bucket whose %s segment was clicked, past days with none",
    async (series) => {
      openStats({
        "stats.get": () =>
          stats({
            buckets: [
              { ...summary({ completed: 1 }), start: "2026-09-27" },
              { ...summary({ completed: 10, failed: 2, interrupted: 4 }), start: "2026-09-28" },
            ],
          }),
      })
      await screen.findByRole("region", { name: "Turns" })

      clickBar(0, series)

      expect(screen.getByRole("table", { name: "Turns closed on Sep 28" })).toBeDefined()
    },
  )

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

describe("an older core", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/instances/hub-ada/stats")
  })

  // A core from before plan_use answers stats.get without it, and its summaries have no origins.
  const older = {
    ...stats(),
    plan_use: undefined,
    total: { ...summary(), origins: undefined },
  } as unknown as StatsGetResult

  it("says its answer needs an update, shows nothing from it, and opens Package and version", async () => {
    openStats({ "stats.get": () => older })
    const user = userEvent.setup()

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("This instance runs an older core")
    expect(screen.queryByRole("region", { name: "Turns" })).toBeNull()
    await user.click(within(alert).getByRole("button", { name: "Open Package and version" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/config/package")
  })

  it("says the same when its core has no stats.get", async () => {
    openStats({})

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("This instance runs an older core")
    expect(alert.textContent).not.toContain("The stub has no answer")
  })
})

async function openTab(name: string) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole("tab", { name }))
}

describe("the Spend tab", () => {
  it("shows the range's usage by source: API tokens and cost, and each plan's runs", async () => {
    openStats({
      "stats.get": () =>
        stats({
          total: summary({
            completed: 4,
            input_tokens: 12_000,
            output_tokens: 3_400,
            cost: 1.236,
            subscriptions: [
              {
                usage_source: "claude-subscription",
                runs: 3,
                input_tokens: 40_000,
                output_tokens: 2_000,
                duration_ms: 5_400_000,
              },
              {
                usage_source: "chatgpt-subscription",
                runs: 1,
                input_tokens: 900,
                output_tokens: 100,
                duration_ms: 95_000,
              },
            ],
          }),
        }),
    })

    await openTab("Spend")

    expect(rows(screen.getByRole("table", { name: "Usage by source" }))).toEqual([
      ["API", "—", "15,400", "—", "$1.24"],
      ["Claude", "3", "42,000", "1h 30m", "not priced"],
      ["ChatGPT", "1", "1,000", "1m 35s", "not priced"],
    ])
  })
  it("names the unpriced models, since the cost it shows leaves their turns out", async () => {
    openStats({ "stats.get": () => stats({ unpriced_models: ["mystery"] }) })

    await openTab("Spend")

    expect(screen.getByText("No price for mystery")).toBeDefined()
  })

  it("shows each bucket's API cost, an unpriced one as not priced, an empty one as $0.00", async () => {
    openStats({
      "stats.get": () =>
        stats({
          buckets: [
            { ...summary({ completed: 2, cost: 0.5 }), start: "2026-09-27" },
            { ...summary({ completed: 1, cost: null }), start: "2026-09-28" },
            { ...summary({ completed: 3, cost: 1.236 }), start: "2026-09-29" },
          ],
          total: summary({ completed: 6, cost: 1.736 }),
        }),
    })

    await openTab("Spend")

    expect(bucketTooltips(screen.getByRole("region", { name: "API cost" }), 7)).toEqual([
      "Sep 24API cost$0.00",
      "Sep 25API cost$0.00",
      "Sep 26API cost$0.00",
      "Sep 27API cost$0.50",
      "Sep 28API costnot priced",
      "Sep 29API cost$1.24",
      "Sep 30API cost$0.00",
    ])
  })

  it("ticks the API cost axis on evenly spaced whole cents", async () => {
    openStats({
      "stats.get": () =>
        stats({
          buckets: [
            { ...summary({ completed: 1, cost: 0.07 }), start: "2026-09-27" },
            { ...summary({ completed: 2, cost: 0.18 }), start: "2026-09-28" },
          ],
        }),
    })

    await openTab("Spend")

    expect(axisTicks(screen.getByRole("region", { name: "API cost" }))).toEqual([
      "$0.00",
      "$0.05",
      "$0.10",
      "$0.15",
      "$0.20",
    ])
  })

  it("ticks an API cost over a dollar on whole cents, with no label repeated", async () => {
    openStats({
      "stats.get": () =>
        stats({
          buckets: [
            { ...summary({ completed: 2, cost: 0.5 }), start: "2026-09-27" },
            { ...summary({ completed: 3, cost: 12.345 }), start: "2026-09-28" },
          ],
        }),
    })

    await openTab("Spend")

    expect(axisTicks(screen.getByRole("region", { name: "API cost" }))).toEqual([
      "$0.00",
      "$3.50",
      "$7.00",
      "$10.50",
      "$14.00",
    ])
  })

  it("says so when no turn in the range was priced, and never shows the cost as 0", async () => {
    openStats({
      "stats.get": () =>
        stats({
          buckets: [
            { ...summary({ completed: 2 }), start: "2026-09-27" },
            { ...summary({ completed: 1 }), start: "2026-09-28" },
          ],
          total: summary({ completed: 3, input_tokens: 800 }),
          unpriced_models: ["ollama:llama3"],
        }),
    })

    await openTab("Spend")

    const cost = screen.getByRole("region", { name: "API cost" })
    expect(within(cost).getByText("No turn in this range was priced.")).toBeDefined()
    expect(cost.querySelector("svg.recharts-surface")).toBeNull()
    expect(rows(screen.getByRole("table", { name: "Usage by source" }))[0]).toEqual([
      "API",
      "—",
      "800",
      "—",
      "not priced",
    ])
    expect(within(screen.getByRole("tabpanel")).queryByText(/\$0/)).toBeNull()
  })
  it("shows each plan's runs in each bucket", async () => {
    const runs = (claude: number, chatgpt: number) => [
      { usage_source: "claude-subscription" as const, runs: claude },
      { usage_source: "chatgpt-subscription" as const, runs: chatgpt },
    ]
    openStats({
      "stats.get": () =>
        stats({
          buckets: [
            { ...summary({ subscriptions: runs(2, 1) }), start: "2026-09-27" },
            { ...summary({ subscriptions: runs(0, 3) }), start: "2026-09-28" },
          ],
        }),
    })

    await openTab("Spend")

    const chart = screen.getByRole("region", { name: "Plan runs" })
    expect(legend(chart)).toBe("ChatGPTClaude")
    expect(bucketTooltips(chart, 7)).toEqual([
      "Sep 24Claude0ChatGPT0",
      "Sep 25Claude0ChatGPT0",
      "Sep 26Claude0ChatGPT0",
      "Sep 27Claude2ChatGPT1",
      "Sep 28Claude0ChatGPT3",
      "Sep 29Claude0ChatGPT0",
      "Sep 30Claude0ChatGPT0",
    ])
  })
})

describe("the Quality tab", () => {
  it("counts the range's tools, memory calls, approvals and denials, and ratings", async () => {
    openStats({
      "stats.get": () =>
        stats({
          total: summary({
            completed: 6,
            tool_calls: { read_file: 12, bash: 30, write_file: 4 },
            memory_calls: { search: 5, open: 3, remember: 1 },
            turns_without_memory: 2,
            approvals_requested: 4,
            denies: { policy: 1, user: 2 },
            good_ratings: 3,
            bad_ratings: 1,
          }),
        }),
    })

    await openTab("Quality")

    const region = (name: string) => counts(screen.getByRole("region", { name }))
    expect(region("Most-called tools")).toEqual([
      ["bash", "30"],
      ["read_file", "12"],
      ["write_file", "4"],
    ])
    expect(region("Memory")).toEqual([
      ["Searches", "5"],
      ["Opens", "3"],
      ["Remembered", "1"],
      ["Forgotten", "0"],
      ["Turns without memory", "2"],
    ])
    expect(region("Approvals")).toEqual([
      ["Asked", "4"],
      ["Denied by policy", "1"],
      ["Denied by you", "2"],
    ])
    expect(region("Ratings")).toEqual([
      ["Good", "3"],
      ["Bad", "1"],
    ])
  })
  it("shows each bucket's mean reads before the first write, over the turns that wrote", async () => {
    const navigated = (turnId: string, closedAt: string, reads: number, writes: number) =>
      turn({
        turn_id: turnId,
        closed_at: closedAt,
        navigation: { read_calls: reads + 4, reads_before_first_write: reads, write_calls: writes },
      })
    openStats({
      "stats.get": () =>
        stats({
          records: [
            navigated("fix", "2026-09-27T09:00:00Z", 6, 2),
            navigated("refactor", "2026-09-27T17:00:00Z", 3, 1),
            navigated("look around", "2026-09-27T18:00:00Z", 10, 0),
            navigated("rename", "2026-09-28T08:00:00Z", 2, 1),
          ],
          buckets: [
            { ...summary({ completed: 3 }), start: "2026-09-27" },
            { ...summary({ completed: 1 }), start: "2026-09-28" },
          ],
        }),
    })

    await openTab("Quality")

    expect(bucketTooltips(screen.getByRole("region", { name: "Navigation" }), 7)).toEqual([
      "",
      "",
      "",
      "Sep 27Reads before the first write4.5",
      "Sep 28Reads before the first write2",
      "",
      "",
    ])
  })

  it("hides the navigation trend when no turn in the range navigated", async () => {
    openStats({
      "stats.get": () =>
        stats({
          records: [turn({ turn_id: "chat", navigation: { read_calls: 3, write_calls: 0 } })],
          buckets: [{ ...summary({ completed: 1 }), start: "2026-09-28" }],
        }),
    })

    await openTab("Quality")

    expect(screen.getByRole("region", { name: "Ratings" })).toBeDefined()
    expect(screen.queryByRole("region", { name: "Navigation" })).toBeNull()
  })
})

function routine(fields: Partial<RoutineSummary> & Pick<RoutineSummary, "name">): RoutineSummary {
  return {
    description: "",
    schedule: null,
    enabled: true,
    mode: "auto",
    next_run: null,
    last_run: null,
    failure_count: 0,
    pending: 0,
    ...fields,
  }
}

describe("the Origin tab", () => {
  const byOrigin = stats({
    records: [
      turn({ turn_id: "chat", cost: 0.5 }),
      turn({
        turn_id: "inbox",
        cost: 1.2,
        origin: { kind: "routine", name: "inbox", trigger: "scheduled" },
      }),
      turn({ turn_id: "unknown", cost: 0.1, origin: null }),
      turn({
        turn_id: "inbox next day",
        closed_at: "2026-09-29T08:00:00Z",
        cost: 0.7,
        origin: { kind: "routine", name: "inbox", trigger: "manual" },
      }),
    ],
    buckets: [
      { ...summary({ completed: 3 }), start: "2026-09-28" },
      { ...summary({ completed: 1 }), start: "2026-09-29" },
    ],
    total: summary({
      completed: 4,
      origins: [
        originUse(null, {
          turns: 2,
          cost: 0.6,
          runs: [
            { usage_source: "claude-subscription", runs: 3 },
            { usage_source: "chatgpt-subscription", runs: 1 },
          ],
        }),
        originUse("inbox", { turns: 2, no_work: 5, failed: 1, cost: 1.9 }),
        originUse("morning", { turns: 1 }),
      ],
    }),
  })

  function openOrigins(routines: RoutineSummary[] = [routine({ name: "inbox" })]) {
    return openStats({
      "stats.get": () => byOrigin,
      "routine.list": () => ({ routines, warnings: [] }),
    })
  }

  it("shows a row for chat and one per routine: turns, no work, failed, API cost, plan runs", async () => {
    openOrigins()

    await openTab("Origin")

    const table = screen.getByRole("table", { name: "Turns by origin" })
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent),
    ).toEqual(["Origin", "Turns", "No work", "Failed", "API cost", "Claude runs", "ChatGPT runs"])
    await within(table).findByRole("link", { name: "inbox" })
    expect(rows(table)).toEqual([
      ["Chat", "2", "0", "0", "$0.60", "3", "1"],
      ["inbox", "2", "5", "1", "$1.90", "0", "0"],
      ["morningremoved or renamed", "1", "0", "0", "not priced", "0", "0"],
    ])
  })

  it("links a routine to it in the config panel, and a name routine.list lacks to nothing", async () => {
    window.history.replaceState(null, "", "/instances/hub-ada/stats")
    openOrigins()
    const user = userEvent.setup()
    await openTab("Origin")
    const table = screen.getByRole("table", { name: "Turns by origin" })

    await user.click(await within(table).findByRole("link", { name: "inbox" }))

    expect(within(table).queryByRole("link", { name: /morning/ })).toBeNull()
    expect(window.location.pathname).toBe("/instances/hub-ada/config/routines/inbox")
  })

  it("filters the drill-down to a clicked row, here and in the Overview", async () => {
    openOrigins()
    const user = userEvent.setup()
    await openTab("Origin")
    const origins = screen.getByRole("table", { name: "Turns by origin" })
    const costs = (name: string) => rows(screen.getByRole("table", { name })).map((row) => row[4])

    await user.click(within(origins).getByRole("row", { name: /^Chat/ }))
    expect(costs("Chat turns closed in this range")).toEqual(["$0.50", "$0.10"])
    await user.click(within(origins).getByRole("row", { name: /^inbox/ }))
    expect(costs("inbox turns closed in this range")).toEqual(["$1.20", "$0.70"])

    await openTab("Overview")
    clickBar(0)
    expect(costs("inbox turns closed on Sep 28")).toEqual(["$1.20"])

    await openTab("Origin")
    await user.click(
      within(screen.getByRole("table", { name: "Turns by origin" })).getByRole("row", {
        name: /^inbox/,
      }),
    )
    expect(costs("Turns closed on Sep 28")).toEqual(["$1.20", "$0.50", "$0.10"])
  })

  it("lists each routine's last outcome, failures in a row, and next run below the table", async () => {
    openOrigins([
      routine({
        name: "inbox",
        next_run: new Date(2026, 9, 1, 9, 0).toISOString(),
        last_run: {
          started_at: new Date(2026, 8, 30, 9, 0).toISOString(),
          outcome: "no-work",
          thread_id: "thread-1",
          turn_id: "turn-1",
        },
      }),
      routine({ name: "triage", failure_count: 3 }),
    ])

    await openTab("Origin")

    const listed = await screen.findByRole("list", { name: "Routines" })
    const inbox = within(listed).getByRole("listitem", { name: "inbox" }).textContent
    expect(inbox).toContain("nothing new")
    expect(inbox).toContain("No failures")
    expect(inbox).toContain("Next firing")
    const triage = within(listed).getByRole("listitem", { name: "triage" }).textContent
    expect(triage).toContain("Never fired")
    expect(triage).toContain("3 failures in a row")
    expect(triage).toContain("No next firing")
  })
})
