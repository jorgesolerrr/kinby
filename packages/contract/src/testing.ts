import type { Clock, Socket, SocketEvents, Transport } from "./client"
import type { InstanceSummary } from "./contract"

/** The one token the fake hub accepts at its login route. */
export const ACCESS_TOKEN = "the-access-token"

export interface FakeSocket extends Socket {
  readonly url: string
  /** The frames the client sent, parsed. */
  readonly sent: unknown[]
  /** Deliver a frame from the hub, as text the way the socket carries it. */
  receive(frame: unknown): void
  /** The connection drops under the client. */
  drop(): void
}

export interface FakeHub {
  readonly transport: Transport
  /** Every socket the client opened, oldest first. */
  readonly sockets: FakeSocket[]
  /** Whether the browser session is open. Setting it false is what a token rotation does. */
  signedIn: boolean
  /** False while the hub is down, as during a restart: no upgrade and no route answers. */
  reachable: boolean
  /** What `instance.list` answers with. */
  instances: InstanceSummary[]
}

/** A hub that answers like the real one: the socket upgrades only while the browser session is open. */
export function fakeHub({
  signedIn = true,
  instances = [],
}: { signedIn?: boolean; instances?: InstanceSummary[] } = {}): FakeHub {
  const hub: FakeHub = {
    signedIn,
    reachable: true,
    instances,
    sockets: [],
    transport: {
      openSocket(url, events) {
        const socket = fakeSocket(url, events, (method) =>
          method === "instance.list" ? { instances: hub.instances } : undefined,
        )
        hub.sockets.push(socket)
        queueMicrotask(() => (hub.reachable && hub.signedIn ? socket.accept() : socket.drop()))
        return socket
      },
      async fetch(url, init) {
        if (!hub.reachable) throw new TypeError("Failed to fetch")
        switch (`${init?.method ?? "GET"} ${new URL(url).pathname}`) {
          case "POST /auth/login":
            if (!carriesAccessToken(init?.body)) return new Response(null, { status: 401 })
            hub.signedIn = true
            return new Response(null, { status: 200 })
          case "POST /auth/logout":
            hub.signedIn = false
            return new Response(null, { status: 204 })
          case "GET /auth/session":
            return new Response(null, { status: hub.signedIn ? 204 : 401 })
          default:
            return new Response(null, { status: 404 })
        }
      },
    },
  }
  return hub
}

/** Build an instance as `instance.list` reports it, filling in what a test does not care about. */
export function instanceSummary(
  fields: Pick<InstanceSummary, "instance_id"> & Partial<InstanceSummary>,
): InstanceSummary {
  return {
    image_id: "sha256:image",
    intended_state: "running",
    manifest_id: fields.instance_id,
    persona_name: null,
    runtime_id: "runtime",
    source_revision: "revision",
    storage: [],
    ...fields,
  }
}

export interface FakeClock extends Clock {
  /** What every random draw returns. Full jitter scales the backoff ceiling by it. */
  draw: number
  /** Let the client settle, then let `ms` pass, running each callback as it falls due. */
  advance(ms: number): Promise<void>
}

/** A clock that stands still until the test moves it, drawing the same number every time. */
export function fakeClock({ draw = 0.5 } = {}): FakeClock {
  let now = 0
  const timers = new Set<{ at: number; callback: () => void }>()
  // The fake hub answers in microtasks, so one real macrotask lets them all run.
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0))
  return {
    draw,
    random() {
      return this.draw
    },
    after(ms, callback) {
      const timer = { at: now + ms, callback }
      timers.add(timer)
      return () => timers.delete(timer)
    },
    async advance(ms) {
      await settle()
      now += ms
      for (;;) {
        const [due] = [...timers].filter((timer) => timer.at <= now).sort((a, b) => a.at - b.at)
        if (due === undefined) return
        timers.delete(due)
        due.callback()
        await settle()
      }
    },
  }
}

function fakeSocket(
  url: string,
  events: SocketEvents,
  answer: (method: unknown) => object | undefined,
): FakeSocket & { accept(): void } {
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    events.close()
  }
  const receive = (frame: unknown) =>
    events.message(typeof frame === "string" ? frame : JSON.stringify(frame))
  return {
    url,
    sent: [],
    // A call the hub knows is answered on the next tick, unless the socket drops first.
    send(data) {
      const frame: unknown = JSON.parse(data)
      this.sent.push(frame)
      if (!isRecord(frame) || frame.type !== "call") return
      const result = answer(frame.method)
      if (result === undefined) return
      queueMicrotask(() => {
        if (!closed) receive({ type: "result", id: frame.id, result })
      })
    },
    close,
    receive,
    drop: close,
    accept: () => {
      if (!closed) events.open()
    },
  }
}

function carriesAccessToken(body: unknown): boolean {
  if (typeof body !== "string") return false
  const login: unknown = JSON.parse(body)
  return (
    typeof login === "object" && login !== null && "token" in login && login.token === ACCESS_TOKEN
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
