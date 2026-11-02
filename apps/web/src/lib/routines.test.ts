import type { RoutineSummary } from "@kinby/contract"
import { describe, expect, it } from "vitest"

import { triggerInWords } from "@/lib/routines"

const routine: RoutineSummary = {
  name: "news",
  description: "Morning news",
  schedule: null,
  enabled: true,
  mode: "auto",
  next_run: null,
  last_run: null,
  pending: 0,
}

describe("triggerInWords", () => {
  it.each([
    ["0 * * * *", "Every hour"],
    ["0 */1 * * *", "Every hour"],
    ["15 */6 * * *", "Every 6 hours"],
    ["0 9 * * *", "Daily at 09:00"],
    ["30 7 * * 1-5", "Weekdays at 07:30"],
    ["0 18 * * 0", "Weekly on Sunday at 18:00"],
    ["0 18 * * 7", "Weekly on Sunday at 18:00"],
    ["5 8 * * 3", "Weekly on Wednesday at 08:05"],
    ["0 20 * 6-9 *", "0 20 * 6-9 *"],
    ["0 9 1 * *", "0 9 1 * *"],
    ["0 9 * * 1,3", "0 9 * * 1,3"],
    ["*/15 * * * *", "*/15 * * * *"],
    ["@daily", "@daily"],
  ])("says the schedule %s as %s", (schedule, words) => {
    expect(triggerInWords({ ...routine, schedule })).toBe(words)
  })

  it("says a signal's path", () => {
    expect(
      triggerInWords({ ...routine, signal: { path: "/signals/issues", auth: "hmac-sha256" } }),
    ).toBe("When a signal arrives at /signals/issues")
  })

  it("says a routine with neither starts only by hand", () => {
    expect(triggerInWords(routine)).toBe("Only when started by hand")
  })
})
