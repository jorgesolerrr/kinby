import { describe, expect, expectTypeOf, it, vi } from "vitest"

import { CallError, createClient, type Transport } from "./client"
import type { InstanceListResult, InstanceStatusResult } from "./index"
import { fakeHub } from "./testing"

const ORIGIN = "https://hub.example"

async function connected(hub = fakeHub()) {
  const client = createClient(ORIGIN, hub.transport)
  await vi.waitFor(() => expect(client.state()).toBe("connected"))
  const socket = hub.sockets[0]
  if (socket === undefined) throw new Error("the client opened no socket")
  return { client, hub, socket }
}

describe("client", () => {
  it("opens one socket at the page's origin and answers a call with its result", async () => {
    const { client, hub, socket } = await connected()

    const listed = client.call("instance.list", {})
    const [call] = socket.sent
    socket.receive({ type: "result", id: "1", result: { instances: [] } })

    expect(hub.sockets.map((opened) => opened.url)).toEqual(["wss://hub.example/ws"])
    expect(call).toEqual({ type: "call", id: "1", method: "instance.list", params: {} })
    await expect(listed).resolves.toEqual({ instances: [] })
  })

  it("types a call from its method name to the method's command and result", async () => {
    const { client } = await connected()

    expectTypeOf(client.call("instance.list", {})).resolves.toEqualTypeOf<InstanceListResult>()
    expectTypeOf(
      client.call("instance.status", { instance_id: "i" }),
    ).resolves.toEqualTypeOf<InstanceStatusResult>()
    // @ts-expect-error instance.status names the instance it reads
    expectTypeOf(client.call("instance.status", {})).toBeObject()
  })

  it("rejects a call the hub answers with an error frame with a typed error", async () => {
    const { client, socket } = await connected()

    const status = client.call("instance.status", { instance_id: "missing" })
    socket.receive({
      type: "error",
      id: "1",
      error: { code: "NOT_FOUND", message: "no such instance", retryable: false },
    })

    const error = await status.catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(CallError)
    expect(error).toMatchObject({
      code: "NOT_FOUND",
      message: "no such instance",
      retryable: false,
    })
  })

  it("rejects the calls in flight when the socket drops, and sends them nowhere after", async () => {
    const { client, hub, socket } = await connected()

    const listed = client.call("instance.list", {})
    socket.drop()

    await expect(listed).rejects.toMatchObject({ code: "CONNECTION_LOST", retryable: false })
    await vi.waitFor(() => expect(client.state()).toBe("disconnected"))
    await expect(client.call("instance.list", {})).rejects.toBeInstanceOf(CallError)
    expect(socket.sent).toHaveLength(1)
    expect(hub.sockets).toHaveLength(1)
  })

  it("ends signed out when the upgrade is refused, and opens no second socket", async () => {
    const hub = fakeHub({ signedIn: false })
    const client = createClient(ORIGIN, hub.transport)

    await vi.waitFor(() => expect(client.state()).toBe("signed-out"))
    expect(hub.sockets).toHaveLength(1)
  })

  it("ends signed out when a token rotation ended the session and the socket drops", async () => {
    const { client, hub, socket } = await connected()

    hub.signedIn = false
    socket.drop()

    await vi.waitFor(() => expect(client.state()).toBe("signed-out"))
    expect(hub.sockets).toHaveLength(1)
  })

  it("stays signed in, disconnected, while a restarting hub cannot answer", async () => {
    const { client, hub, socket } = await connected()

    hub.reachable = false
    socket.drop()

    await vi.waitFor(() => expect(client.state()).toBe("disconnected"))
  })

  it("keeps signed-out when a session check started by a drop returns after sign-out", async () => {
    const hub = fakeHub()
    let releaseDroppedCheck: (response: Response) => void = () => {}
    let sessionChecks = 0
    const transport: Transport = {
      openSocket: (url, events) => hub.transport.openSocket(url, events),
      fetch: (url, init) => {
        const sessionCheck =
          (init?.method ?? "GET") === "GET" && new URL(url).pathname === "/auth/session"
        if (sessionCheck && sessionChecks++ === 0) {
          return new Promise((resolve) => {
            releaseDroppedCheck = resolve
          })
        }
        return hub.transport.fetch(url, init)
      },
    }
    const client = createClient(ORIGIN, transport)
    await vi.waitFor(() => expect(client.state()).toBe("connected"))
    const socket = hub.sockets[0]
    if (socket === undefined) throw new Error("the client opened no socket")

    socket.drop()
    expect(client.state()).toBe("connected")

    const signingOut = client.signOut()
    await vi.waitFor(() => expect(sessionChecks).toBe(2))
    await signingOut

    expect(client.state()).toBe("signed-out")
    releaseDroppedCheck(new Response(null, { status: 204 }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(client.state()).toBe("signed-out")
    expect(hub.signedIn).toBe(false)
  })
})
