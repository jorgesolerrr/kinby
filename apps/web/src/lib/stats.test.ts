import { describe, expect, it } from "vitest"

import { bucketStarts, contextFill } from "@/lib/stats"

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
