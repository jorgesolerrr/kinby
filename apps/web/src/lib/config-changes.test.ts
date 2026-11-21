import type { ConfigChange, FactoryVersion, FactoryVersionSource } from "@kinby/contract"
import { stubCaller } from "@kinby/contract/testing"
import { describe, expect, it } from "vitest"

import { lastChanged, readWithChange, stepChanged, stepLastChange } from "@/lib/config-changes"

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

describe("readWithChange", () => {
  it("reads again when a save lands between the read and the history", async () => {
    // The first read sees the file before the save, the history already logs it.
    const hashes = ["hash-0", "hash-1"]
    const caller = stubCaller({ "config.history": () => ({ changes: [saved] }) })
    const read = () => Promise.resolve({ hash: hashes.shift() ?? "hash-1" })

    const [result, change] = await readWithChange(caller, "memory/profile.md", read)

    expect(lastChanged(change, result.hash)).toBe("Last changed by you, Sep 28, 2026, 10:04 AM")
  })

  it("keeps a change made outside kinby apart after reading again", async () => {
    const caller = stubCaller({ "config.history": () => ({ changes: [saved] }) })

    const [result, change] = await readWithChange(caller, "memory/profile.md", () =>
      Promise.resolve({ hash: "hash-by-hand" }),
    )

    expect(lastChanged(change, result.hash)).toBe("Changed outside kinby")
    expect(caller.calls).toHaveLength(2)
  })
})

const version = (source: FactoryVersionSource, steps: string[], day = 19): FactoryVersion => ({
  version: `${source}-${day}`,
  time: new Date(2026, 10, day, 10, 0).toISOString(),
  source,
  steps,
})

describe("stepLastChange", () => {
  const versions = [
    version("web", ["review"], 20),
    version("by_hand", ["implement", "review"], 19),
    version("shipped", ["implement", "review", "publish"], 18),
  ]

  it("picks the newest version that lists the step", () => {
    expect(stepLastChange(versions, "review")).toBe(versions[0])
    expect(stepLastChange(versions, "implement")).toBe(versions[1])
    expect(stepLastChange(versions, "publish")).toBe(versions[2])
  })

  it("picks no version for a step no version lists, or a factory without versions", () => {
    expect(stepLastChange(versions, "added-by-hand-in-yaml")).toBeUndefined()
    expect(stepLastChange([], "review")).toBeUndefined()
  })
})

describe("stepChanged", () => {
  it("words each source of a step's last change, with its time", () => {
    expect(stepChanged(version("web", ["review"]))).toBe(
      "Saved from the web on Nov 19, 2026, 10:00 AM",
    )
    expect(stepChanged(version("chat", ["review"]))).toBe(
      "Changed from chat on Nov 19, 2026, 10:00 AM",
    )
    expect(stepChanged(version("by_hand", ["review"]))).toBe(
      "Changed by hand on Nov 19, 2026, 10:00 AM",
    )
    expect(stepChanged(version("revert", ["review"]))).toBe("Reverted on Nov 19, 2026, 10:00 AM")
    expect(stepChanged(version("shipped_update", ["review"]))).toBe(
      "kinby's update, merged on Nov 19, 2026, 10:00 AM",
    )
  })

  it("says a step only kinby's first version lists is kinby's, not the user's change", () => {
    expect(stepChanged(version("shipped", ["review"]))).toBe(
      "kinby's version, unchanged since Nov 19, 2026, 10:00 AM",
    )
  })
})
