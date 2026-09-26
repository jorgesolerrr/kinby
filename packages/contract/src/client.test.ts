import { describe, expect, expectTypeOf, it, vi } from "vitest"

import { CallError, createClient, type Transport } from "./client"
import type { Event, InstanceListResult, InstanceStatusResult } from "./index"
import { type FakeHub, fakeClock, fakeHub } from "./testing"

const ORIGIN = "https://hub.example"

async function connected(hub = fakeHub(), clock = fakeClock()) {
  const client = createClient(ORIGIN, hub.transport, clock)
  await vi.waitFor(() => expect(client.state()).toBe("connected"))
  const socket = hub.sockets[0]
  if (socket === undefined) throw new Error("the client opened no socket")
  return { client, hub, socket, clock }
}

function lastSocket(hub: FakeHub) {
  const socket = hub.sockets.at(-1)
  if (socket === undefined) throw new Error("the client opened no socket")
  return socket
}

function event(sequence: number): Event {
  return {
    sequence,
    thread_id: "t",
    turn_id: "u",
    timestamp: "2026-09-25T10:00:00Z",
    payload: { type: "mode.pinned", mode: "ask" },
  }
}

const item = (sequence: number) => ({ type: "item", id: "1", item: event(sequence) })

async function collect<T>(items: AsyncIterable<T>): Promise<T[]> {
  const collected: T[] = []
  for await (const each of items) collected.push(each)
  return collected
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
      fields: {},
    })
  })

  it("carries what is wrong with each value the hub refused", async () => {
    const { client, socket } = await connected()

    const created = client.call("instance.create", { manifest_id: "Ada", model: "" })
    socket.receive({
      type: "error",
      id: "1",
      error: {
        code: "INVALID_SETUP",
        message: "Some setup values are missing or invalid.",
        retryable: false,
        fields: { model: "Model is required.", api_key: "API key is required." },
      },
    })

    await expect(created).rejects.toMatchObject({
      code: "INVALID_SETUP",
      fields: { model: "Model is required.", api_key: "API key is required." },
    })
  })

  it("rejects the calls in flight when the socket drops, and never replays them", async () => {
    const { client, hub, socket, clock } = await connected()

    const listed = client.call("instance.list", {})
    socket.drop()

    await expect(listed).rejects.toMatchObject({ code: "CONNECTION_LOST", retryable: false })
    await vi.waitFor(() => expect(client.state()).toBe("reconnecting"))
    await expect(client.call("instance.list", {})).rejects.toBeInstanceOf(CallError)
    await clock.advance(16_000)
    expect(client.state()).toBe("connected")
    expect(socket.sent).toHaveLength(1)
    expect(hub.sockets).toHaveLength(2)
    expect(lastSocket(hub).sent).toEqual([])
  })

  it("ends signed out when the upgrade is refused, and opens no second socket", async () => {
    const hub = fakeHub({ signedIn: false })
    const client = createClient(ORIGIN, hub.transport, fakeClock())

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

  it("stays signed in, reconnecting, while a restarting hub cannot answer", async () => {
    const { client, hub, socket } = await connected()

    hub.reachable = false
    socket.drop()

    await vi.waitFor(() => expect(client.state()).toBe("reconnecting"))
  })

  it("delivers a subscription's items in order, and ends on the hub's end frame", async () => {
    const { client, socket } = await connected()

    const events = client.subscribe("thread.subscribe", { thread_id: "t" })
    socket.receive({ type: "subscribed", id: "1", head_sequence: 1 })
    socket.receive(item(1))
    socket.receive(item(2))
    socket.receive({ type: "end", id: "1" })

    expect(socket.sent).toEqual([
      { type: "subscribe", id: "1", method: "thread.subscribe", params: { thread_id: "t" } },
    ])
    expect(await collect(events)).toEqual([event(1), event(2)])
  })

  it("types a subscription from its method name to the method's command and item", async () => {
    const { client } = await connected()

    expectTypeOf(client.subscribe("thread.subscribe", { thread_id: "t" })).toExtend<
      AsyncIterable<Event>
    >()
    // @ts-expect-error thread.subscribe names the thread it follows
    expectTypeOf(client.subscribe("thread.subscribe", {})).toBeObject()
  })

  it("ends a subscription the hub answers with an error frame with a typed error", async () => {
    const { client, socket } = await connected()

    const events = client.subscribe("thread.subscribe", { thread_id: "missing" })
    socket.receive({
      type: "error",
      id: "1",
      error: { code: "NOT_FOUND", message: "no such thread", retryable: false },
    })

    await expect(collect(events)).rejects.toMatchObject({ code: "NOT_FOUND" })
  })

  it("resubscribes after a reconnect from the last sequence seen, dropping replays", async () => {
    const { client, hub, socket, clock } = await connected()

    const events = client.subscribe("thread.subscribe", { thread_id: "t" })
    socket.receive(item(1))
    socket.receive(item(2))
    socket.drop()
    await clock.advance(16_000)
    const reopened = lastSocket(hub)
    reopened.receive(item(2))
    reopened.receive(item(3))
    reopened.receive({ type: "end", id: "1" })

    expect(reopened.sent).toEqual([
      {
        type: "subscribe",
        id: "1",
        method: "thread.subscribe",
        params: { thread_id: "t", after_sequence: 2 },
      },
    ])
    expect(await collect(events)).toEqual([event(1), event(2), event(3)])
  })

  it("sends a subscription opened while reconnecting once the socket is back", async () => {
    const { client, hub, socket, clock } = await connected()

    socket.drop()
    await clock.advance(0)
    const events = client.subscribe("thread.subscribe", { thread_id: "t" })
    await clock.advance(16_000)
    lastSocket(hub).receive(item(1))
    lastSocket(hub).receive({ type: "end", id: "1" })

    expect(lastSocket(hub).sent).toEqual([
      { type: "subscribe", id: "1", method: "thread.subscribe", params: { thread_id: "t" } },
    ])
    expect(await collect(events)).toEqual([event(1)])
  })

  it("ends a subscription on cancel, telling the hub, and never resubscribes it", async () => {
    const { client, hub, socket, clock } = await connected()

    const events = client.subscribe("thread.subscribe", { thread_id: "t" })
    const reader = events[Symbol.asyncIterator]()
    socket.receive(item(1))
    const first = await reader.next()
    events.cancel()
    socket.receive(item(2))
    const afterCancel = await reader.next()
    socket.drop()
    await clock.advance(16_000)

    expect(first).toEqual({ done: false, value: event(1) })
    expect(afterCancel).toEqual({ done: true, value: undefined })
    expect(socket.sent.at(-1)).toEqual({ type: "cancel", id: "1" })
    expect(lastSocket(hub).sent).toEqual([])
  })

  it("cancels a subscription its reader stops reading", async () => {
    const { client, socket } = await connected()

    const events = client.subscribe("thread.subscribe", { thread_id: "t" })
    socket.receive(item(1))
    for await (const _ of events) break

    expect(socket.sent.at(-1)).toEqual({ type: "cancel", id: "1" })
  })

  it("waits a full-jitter backoff before each try, its ceiling doubling up to 16 s", async () => {
    // A draw at the top of its range waits the whole ceiling.
    const { client, hub, socket, clock } = await connected(fakeHub(), fakeClock({ draw: 1 }))

    hub.reachable = false
    socket.drop()
    for (const [tries, ceiling] of [500, 1000, 2000, 4000, 8000, 16_000, 16_000].entries()) {
      await clock.advance(ceiling - 1)
      expect(hub.sockets).toHaveLength(tries + 1)
      await clock.advance(1)
      expect(hub.sockets).toHaveLength(tries + 2)
    }
    expect(client.state()).toBe("reconnecting")
  })

  it("tries again at once when the draw is at the bottom of its range", async () => {
    const { client, hub, socket, clock } = await connected(fakeHub(), fakeClock({ draw: 0 }))

    socket.drop()
    await clock.advance(0)

    expect(hub.sockets).toHaveLength(2)
    expect(client.state()).toBe("connected")
  })

  it("starts the backoff over once the socket has held for 30 s", async () => {
    const { hub, socket, clock } = await connected(fakeHub(), fakeClock({ draw: 1 }))

    socket.drop()
    await clock.advance(500)
    lastSocket(hub).drop()
    await clock.advance(1000)
    expect(hub.sockets).toHaveLength(3)

    await clock.advance(30_000)
    lastSocket(hub).drop()
    await clock.advance(500)

    expect(hub.sockets).toHaveLength(4)
  })

  it("ends signed out when the session ended during a reconnect, and stops trying", async () => {
    const { client, hub, socket, clock } = await connected(fakeHub(), fakeClock({ draw: 1 }))

    hub.reachable = false
    socket.drop()
    await clock.advance(500)
    // The hub comes back from its restart with the token rotated.
    hub.reachable = true
    hub.signedIn = false
    await clock.advance(1000)
    await clock.advance(60_000)

    expect(client.state()).toBe("signed-out")
    expect(hub.sockets).toHaveLength(3)
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
    const clock = fakeClock()
    const client = createClient(ORIGIN, transport, clock)
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
    await clock.advance(60_000)
    expect(client.state()).toBe("signed-out")
    expect(hub.signedIn).toBe(false)
    expect(hub.sockets).toHaveLength(1)
  })
})
