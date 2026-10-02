import type { ConfigChange } from "@kinby/contract"
import { describe, expect, it } from "vitest"

import { lastChanged } from "@/lib/config-changes"

// A local time, so the line shows the same clock time wherever the test runs.
const saved: ConfigChange = {
  at: new Date(2026, 8, 28, 10, 4).toISOString(),
  file: "memory/profile.md",
  actor: "app",
  diff: "",
  hash: "hash-1",
}

describe("lastChanged", () => {
  it("names who changed the file when it is as the change left it", () => {
    expect(lastChanged(saved, "hash-1")).toBe("Last changed by you, Sep 28, 2026, 10:04 AM")
  })

  it("says the file changed outside kinby when it differs from what the change left", () => {
    expect(lastChanged(saved, "hash-2")).toBe("Changed outside kinby")
  })

  it("names who changed the file when the change predates its hash", () => {
    const { hash: _, ...logged } = saved
    expect(lastChanged(logged, "hash-2")).toBe("Last changed by you, Sep 28, 2026, 10:04 AM")
    expect(lastChanged({ ...saved, hash: null }, "hash-2")).toBe(
      "Last changed by you, Sep 28, 2026, 10:04 AM",
    )
  })

  it("says a file nobody changed was never changed", () => {
    expect(lastChanged(undefined, "hash-1")).toBe("Never changed")
  })
})
