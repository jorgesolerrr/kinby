import { describe, expect, it } from "vitest"

import { type Connection, INITIAL_CONNECTION, project } from "./connection"

describe("project", () => {
  it("connects, then reconnects after a drop, counting the drops", () => {
    const opened = project(INITIAL_CONNECTION, "opened")
    const dropped = project(opened, "dropped")
    const droppedAgain = project(project(dropped, "opened"), "dropped")

    expect(opened).toEqual({ state: "connected", drops: 0 })
    expect(dropped).toEqual({ state: "reconnecting", drops: 1 })
    expect(droppedAgain).toEqual({ state: "reconnecting", drops: 2 })
  })

  it("forgets the drops once the socket holds", () => {
    const shaky: Connection = { state: "connected", drops: 3 }

    expect(project(shaky, "steady")).toEqual({ state: "connected", drops: 0 })
  })

  it("ends signed out when the session is gone, and connects again on sign-in", () => {
    const reconnecting: Connection = { state: "reconnecting", drops: 4 }

    const signedOut = project(reconnecting, "session-ended")

    expect(signedOut).toEqual({ state: "signed-out", drops: 0 })
    expect(project(signedOut, "signed-in")).toEqual({ state: "connecting", drops: 0 })
  })
})
