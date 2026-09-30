import type { PlanUse } from "@kinby/contract"
import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { PlansStrip } from "@/components/plans-strip"

const FIVE_HOURS = 18_000
const SEVEN_DAYS = 604_800

const PLAN_USE: PlanUse[] = [
  { usage_source: "claude-subscription", duration_seconds: FIVE_HOURS, runs: 2 },
  { usage_source: "claude-subscription", duration_seconds: SEVEN_DAYS, runs: 9 },
  { usage_source: "chatgpt-subscription", duration_seconds: FIVE_HOURS, runs: 0 },
  { usage_source: "chatgpt-subscription", duration_seconds: SEVEN_DAYS, runs: 3 },
]

function badge(text: string) {
  return screen.getByText(text).closest("[data-slot=badge]")
}

describe("the plans strip", () => {
  it("gives each subscription one badge with its 5-hour and 7-day runs", () => {
    render(<PlansStrip planUse={PLAN_USE} limits={[]} />)

    expect(badge("Claude runs: 2 in 5h · 9 in 7d")?.getAttribute("data-variant")).toBe("outline")
    expect(badge("ChatGPT runs: 0 in 5h · 3 in 7d")?.getAttribute("data-variant")).toBe("outline")
  })

  it("turns a limited subscription's badge red, with its reset in local time", () => {
    const resetsAt = new Date(2026, 8, 30, 18, 40).toISOString()

    render(
      <PlansStrip
        planUse={PLAN_USE}
        limits={[{ usage_source: "claude-subscription", resets_at: resetsAt }]}
      />,
    )

    expect(
      badge("Claude runs: 2 in 5h · 9 in 7d · limited until 18:40")?.getAttribute("data-variant"),
    ).toBe("destructive")
    expect(badge("ChatGPT runs: 0 in 5h · 3 in 7d")?.getAttribute("data-variant")).toBe("outline")
  })
})
