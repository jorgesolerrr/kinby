import { describe, expect, it } from "vitest"

import {
  bucketStarts,
  contextAgainst,
  contextFill,
  contextLimit,
  contextRows,
  effectiveTrigger,
  turnFigures,
} from "@/lib/stats"

const now = new Date("2026-09-30T15:20:00Z")

describe("the bucket starts of a range", () => {
  it("are its 7 UTC days, ending today", () => {
    expect(bucketStarts(7, "day", now)).toEqual([
      "2026-09-24",
      "2026-09-25",
      "2026-09-26",
      "2026-09-27",
      "2026-09-28",
      "2026-09-29",
      "2026-09-30",
    ])
  })

  it("are its 30 UTC days, from the first one through today", () => {
    const starts = bucketStarts(30, "day", now)

    expect(starts).toHaveLength(30)
    expect([starts[0], starts[1], starts[29]]).toEqual(["2026-09-01", "2026-09-02", "2026-09-30"])
  })

  it("count today by its UTC date, not the local one", () => {
    expect(bucketStarts(7, "day", new Date("2026-09-30T23:30:00-05:00")).at(-1)).toBe("2026-10-01")
  })

  it("are a quarter's Mondays, from the week of its first day through this week", () => {
    // The 90 days run from Friday Jul 3 to Wednesday Sep 30.
    expect(bucketStarts(90, "week", now)).toEqual([
      "2026-06-29",
      "2026-07-06",
      "2026-07-13",
      "2026-07-20",
      "2026-07-27",
      "2026-08-03",
      "2026-08-10",
      "2026-08-17",
      "2026-08-24",
      "2026-08-31",
      "2026-09-07",
      "2026-09-14",
      "2026-09-21",
      "2026-09-28",
    ])
  })
})

describe("a context size's fill", () => {
  it("is its share of the model's window, in percent", () => {
    expect(contextFill(50_000, 200_000)).toBe(25)
  })

  it("stops at full when the context size passes the window", () => {
    expect(contextFill(210_000, 200_000)).toBe(100)
  })
})

describe("what the bar fills toward", () => {
  const automatic = { automatic: true, trigger: 100_000 }

  it("is the compaction trigger on a model whose window leaves room for it", () => {
    expect(contextLimit(automatic, 1_000_000)).toBe(100_000)
  })

  it("is the window less 16,000 tokens when that is smaller than the trigger", () => {
    expect(contextLimit(automatic, 64_000)).toBe(48_000)
  })

  it("is the model's window when the window is no larger than the 16,000-token reserve", () => {
    expect(contextLimit(automatic, 8_000)).toBe(8_000)
    expect(contextLimit(automatic, 16_000)).toBe(16_000)
  })

  it("is the trigger on a model with no known window", () => {
    expect(contextLimit(automatic, undefined)).toBe(100_000)
  })

  it("is the model's window with automatic compaction off", () => {
    expect(contextLimit({ automatic: false, trigger: 100_000 }, 200_000)).toBe(200_000)
  })

  it("is the model's window until the manifest is read", () => {
    expect(contextLimit(undefined, 200_000)).toBe(200_000)
  })
})

describe("the effective compaction trigger", () => {
  it("is none with automatic compaction off", () => {
    expect(effectiveTrigger({ automatic: false, trigger: 100_000 }, 200_000)).toBeUndefined()
  })
})

describe("a context size against a limit", () => {
  it("reads as both sizes and the share, past 100% once it passes the limit", () => {
    expect(contextAgainst(41_000, 100_000)).toBe("41k of 100k, 41%")
    expect(contextAgainst(120_000, 100_000)).toBe("120k of 100k, 120%")
  })
})

describe("a context size's breakdown", () => {
  it("names each row and gives its share of the context size, in percent", () => {
    expect(
      contextRows([
        { category: "system_prompt", tokens: 500 },
        { category: "mcp_tools", tokens: 1_500 },
      ]),
    ).toEqual([
      { label: "System prompt", tokens: 500, share: 25 },
      { label: "MCP tools", tokens: 1_500, share: 75 },
    ])
  })
})

describe("a finished turn's figures", () => {
  it("reads steps, cost and time on API use", () => {
    expect(turnFigures({ steps: 3, cost: 0.042, largestContext: 41_000, durationMs: 8_000 })).toBe(
      "3 steps · $0.04 · 8s",
    )
  })

  it("shows the turn's largest context size in place of cost where nothing priced it", () => {
    expect(turnFigures({ steps: 1, largestContext: 41_000, durationMs: 65_000 })).toBe(
      "1 step · 41k context · 1m 5s",
    )
  })

  it("shows steps and time alone for a turn with neither", () => {
    expect(turnFigures({ steps: 0, durationMs: 2_000 })).toBe("0 steps · 2s")
  })
})
