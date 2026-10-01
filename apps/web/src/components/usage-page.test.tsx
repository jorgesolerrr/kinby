import type { StatsBucket, StatsSummaryResult } from "@kinby/contract"
import { type Answers, fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { axisTicks, bucketTooltips, legend, rows } from "@/components/stats-testing"
import { UsagePage } from "@/components/usage-page"

const ada = instanceSummary({ instance_id: "hub-ada", persona_name: "Ada" })
const research = instanceSummary({ instance_id: "hub-research", manifest_id: "research" })
const idle = instanceSummary({
  instance_id: "hub-idle",
  persona_name: "Idle",
  intended_state: "stopped",
  process: "stopped",
})
const far = instanceSummary({ instance_id: "hub-far", persona_name: "Far" })
const old = instanceSummary({ instance_id: "hub-old", persona_name: "Old" })

function bucket(start: string, fields: Partial<StatsBucket> = {}): StatsBucket {
  return {
    start,
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
    origins: [],
    ...fields,
  }
}

function usage(fields: Partial<StatsSummaryResult> = {}): StatsSummaryResult {
  return {
    buckets: {},
    api: { input_tokens: 0, output_tokens: 0, cost: null },
    subscriptions: [
      { usage_source: "claude-subscription", runs: 0 },
      { usage_source: "chatgpt-subscription", runs: 0 },
    ],
    plan_use: [],
    limits: [],
    skipped: [],
    unreachable: [],
    outdated: [],
    ...fields,
  }
}

const runs = (claude: number, chatgpt: number): StatsBucket["subscriptions"] => [
  { usage_source: "claude-subscription", runs: claude },
  { usage_source: "chatgpt-subscription", runs: chatgpt },
]

/**
 * Ada ran on two days, research ran nothing, idle is stopped, far did not answer in time, and old
 * runs a core whose answer the hub cannot read.
 */
const hubUsage = usage({
  buckets: {
    "hub-ada": [
      bucket("2026-09-28", {
        completed: 3,
        failed: 1,
        cost: 0.5,
        subscriptions: runs(2, 1),
        origins: [
          {
            origin: "routine",
            routine: "inbox",
            turns: 4,
            no_work: 0,
            failed: 1,
            cost: 0.5,
            runs: [],
          },
        ],
      }),
      bucket("2026-09-29", { completed: 2, interrupted: 1, cost: null, subscriptions: runs(1, 0) }),
    ],
    "hub-research": [],
  },
  api: { input_tokens: 12_000, output_tokens: 3_400, cost: 0.5 },
  subscriptions: [
    { usage_source: "claude-subscription", runs: 3 },
    { usage_source: "chatgpt-subscription", runs: 1 },
  ],
  skipped: ["hub-idle"],
  unreachable: ["hub-far"],
  outdated: ["hub-old"],
})

const hubAnswers: Answers = {
  "stats.summary": () => hubUsage,
  "instance.list": () => ({ instances: [ada, research, idle, far, old] }),
}

function openUsage(
  answers: Answers = {
    "stats.summary": () => usage(),
    "instance.list": () => ({ instances: [ada] }),
  },
) {
  const client = stubCaller(answers)
  render(<UsagePage client={client} clock={fakeClock()} />)
  return client
}

/** What each stats.summary asked for, oldest first. */
function summaryReads(client: ReturnType<typeof stubCaller>) {
  return client.calls.filter((call) => call.method === "stats.summary").map((call) => call.params)
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] })
  vi.setSystemTime(new Date("2026-09-30T15:20:00Z"))
})

afterEach(() => {
  vi.useRealTimers()
})

describe("the usage page", () => {
  it("reads the last 7 days by day and the instances on open", async () => {
    const client = openUsage()

    await act(() => Promise.resolve())

    expect(summaryReads(client)).toEqual([{ since: "2026-09-24T00:00:00.000Z", by: "day" }])
    expect(client.calls.filter((call) => call.method === "instance.list")).toHaveLength(1)
  })

  it("reads 30 days by day and 90 days by week when the range changes", async () => {
    const client = openUsage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "30 days" }))
    await user.click(screen.getByRole("button", { name: "90 days" }))

    expect(summaryReads(client).slice(1)).toEqual([
      { since: "2026-09-01T00:00:00.000Z", by: "day" },
      { since: "2026-07-03T00:00:00.000Z", by: "week" },
    ])
  })

  it("reads again on window focus and on Refresh, and never on its own", async () => {
    const client = stubCaller({
      "stats.summary": () => usage(),
      "instance.list": () => ({ instances: [ada] }),
    })
    const clock = fakeClock()
    render(<UsagePage client={client} clock={clock} />)
    const user = userEvent.setup()
    await act(() => clock.advance(10 * 60_000))
    expect(summaryReads(client)).toHaveLength(1)

    act(() => {
      window.dispatchEvent(new Event("focus"))
    })
    await user.click(screen.getByRole("button", { name: "Refresh" }))

    expect(summaryReads(client)).toEqual(
      Array(3).fill({ since: "2026-09-24T00:00:00.000Z", by: "day" }),
    )
    expect(client.calls.filter((call) => call.method === "instance.list")).toHaveLength(3)
  })

  it("shows each plan's recent runs across the hub, and when a limited one resets", async () => {
    openUsage({
      "stats.summary": () =>
        usage({
          plan_use: [
            { usage_source: "claude-subscription", duration_seconds: 18_000, runs: 4 },
            { usage_source: "claude-subscription", duration_seconds: 604_800, runs: 21 },
            { usage_source: "chatgpt-subscription", duration_seconds: 18_000, runs: 1 },
            { usage_source: "chatgpt-subscription", duration_seconds: 604_800, runs: 6 },
          ],
          limits: [{ usage_source: "chatgpt-subscription", resets_at: "2026-09-30T18:40:00Z" }],
        }),
      "instance.list": () => ({ instances: [ada] }),
    })

    expect(await screen.findByText("Claude runs: 4 in 5h · 21 in 7d")).toBeDefined()
    expect(screen.getByText(/ChatGPT runs: 1 in 5h · 6 in 7d · limited until/)).toBeDefined()
  })

  it("lists each instance's turns, API cost, and plan runs, and why one is not counted", async () => {
    openUsage(hubAnswers)

    const table = await screen.findByRole("table", { name: "Instances" })

    expect(rows(table)).toEqual([
      ["Ada", "7", "$0.50", "3", "1"],
      ["research", "0", "not priced", "0", "0"],
      ["Idle", "Not counted: not running"],
      ["Far", "Not counted: didn't answer"],
      ["Old", "Not counted: runs an older kinby, update core"],
    ])
  })

  it("adds up the counted instances below the table, and says how many were counted", async () => {
    openUsage(hubAnswers)

    const totals = await screen.findByRole("region", { name: "Totals" })
    const tile = (name: string) => within(totals).getByRole("region", { name }).textContent

    expect(tile("Turns")).toBe("Turns71 failed · 1 interrupted")
    expect(tile("API cost")).toBe("API cost$0.5012,000 in · 3,400 out")
    expect(tile("Plan runs")).toBe("Plan runs4Claude 3 · ChatGPT 1")
    expect(within(totals).getByText("2 of 5 instances counted.")).toBeDefined()
    const table = screen.getByRole("table", { name: "Instances" })
    expect(table.compareDocumentPosition(totals)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it("charts each counted instance on every day, on API cost, plan runs, and turns tabs", async () => {
    openUsage({
      "stats.summary": () =>
        usage({
          buckets: {
            "hub-ada": hubUsage.buckets["hub-ada"],
            "hub-research": [
              bucket("2026-09-29", { completed: 1, cost: 0.1, subscriptions: runs(0, 2) }),
            ],
          },
          skipped: ["hub-idle"],
        }),
      "instance.list": () => ({ instances: [ada, research, idle] }),
    })
    const user = userEvent.setup()
    const chart = await screen.findByRole("region", { name: "Per instance" })

    expect(
      within(chart)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["API cost", "Plan runs", "Turns"])
    expect(legend(chart)).toBe("Adaresearch")
    expect(bucketTooltips(chart, 7)).toEqual([
      "Sep 24Ada$0.00research$0.00",
      "Sep 25Ada$0.00research$0.00",
      "Sep 26Ada$0.00research$0.00",
      "Sep 27Ada$0.00research$0.00",
      "Sep 28Ada$0.50research$0.00",
      "Sep 29Adanot pricedresearch$0.10",
      "Sep 30Ada$0.00research$0.00",
    ])
    await user.click(within(chart).getByRole("tab", { name: "Plan runs" }))
    expect(bucketTooltips(chart, 7).slice(3)).toEqual([
      "Sep 27Ada0research0",
      "Sep 28Ada3research0",
      "Sep 29Ada1research2",
      "Sep 30Ada0research0",
    ])
    await user.click(within(chart).getByRole("tab", { name: "Turns" }))
    expect(bucketTooltips(chart, 7).slice(3)).toEqual([
      "Sep 27Ada0research0",
      "Sep 28Ada4research0",
      "Sep 29Ada3research1",
      "Sep 30Ada0research0",
    ])
  })

  it("ticks the API cost tab on evenly spaced whole cents, and the counts on whole numbers", async () => {
    openUsage({
      "stats.summary": () =>
        usage({
          buckets: {
            "hub-ada": [bucket("2026-09-28", { completed: 3, cost: 0.08 })],
            "hub-research": [bucket("2026-09-28", { completed: 1, cost: 0.1 })],
          },
        }),
      "instance.list": () => ({ instances: [ada, research] }),
    })
    const user = userEvent.setup()
    const chart = await screen.findByRole("region", { name: "Per instance" })

    expect(axisTicks(chart)).toEqual(["$0.00", "$0.05", "$0.10", "$0.15", "$0.20"])
    await user.click(within(chart).getByRole("tab", { name: "Turns" }))
    expect(axisTicks(chart)).toEqual(["0", "1", "2", "3", "4"])
  })

  it("splits nothing by origin and measures no quality", async () => {
    openUsage(hubAnswers)

    const table = await screen.findByRole("table", { name: "Instances" })

    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["Instance", "Turns", "API cost", "Claude runs", "ChatGPT runs"])
    expect(screen.queryByText(/inbox/)).toBeNull()
    expect(screen.queryByRole("tab", { name: "Origin" })).toBeNull()
    expect(screen.queryByRole("tab", { name: "Quality" })).toBeNull()
  })

  it("links each instance to its Stats tab", async () => {
    openUsage(hubAnswers)
    const user = userEvent.setup()

    const link = await screen.findByRole("link", { name: "Idle" })
    expect(link.getAttribute("href")).toBe("/instances/hub-idle/stats")
    await user.click(screen.getByRole("link", { name: "Ada" }))

    expect(window.location.pathname).toBe("/instances/hub-ada/stats")
  })
})
