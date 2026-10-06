import {
  CallError,
  type Client,
  type Clock,
  type Command,
  type Item,
  type Method,
  type Result,
  type Socket,
  type SocketEvents,
  type SubscriptionMethod,
  type Transport,
} from "./client"
import type { InstanceSummary, OperationGetResult } from "./contract"

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
  /** What `instance.list` answers with when asked for the removed instances. */
  removed: InstanceSummary[]
}

/**
 * A hub that answers like the real one: the socket upgrades only while the browser session is open.
 * A removal or a restoration moves the instance between the lists at once, and a deletion drops
 * it from the removed list. Their operations have already succeeded when first read. A deletion
 * previews the instance's directory under /srv/kinby/instances.
 */
export function fakeHub({
  signedIn = true,
  instances = [],
  removed = [],
}: {
  signedIn?: boolean
  instances?: InstanceSummary[]
  removed?: InstanceSummary[]
} = {}): FakeHub {
  const operations = new Map<string, OperationGetResult>()
  const finished = (kind: "remove" | "restore" | "delete", instanceId: string) => {
    const operation_id = `op-${operations.size + 1}`
    operations.set(operation_id, {
      operation_id,
      instance_id: instanceId,
      kind,
      state: "succeeded",
      detail: "",
      steps: [],
    })
    return { operation_id, instance_id: instanceId }
  }
  const hub: FakeHub = {
    signedIn,
    reachable: true,
    instances,
    removed,
    sockets: [],
    transport: {
      openSocket(url, events) {
        const socket = fakeSocket(url, events, (method, params) => {
          if (method === "instance.list") {
            return { instances: isRecord(params) && params.removed ? hub.removed : hub.instances }
          }
          if (method === "instance.remove" && isRecord(params)) {
            const removing = hub.instances.find(
              (instance) => instance.instance_id === params.instance_id,
            )
            if (removing === undefined) return undefined
            hub.instances = hub.instances.filter((instance) => instance !== removing)
            hub.removed = [...hub.removed, { ...removing, intended_state: "removed" }]
            return finished("remove", removing.instance_id)
          }
          if (method === "instance.restore" && isRecord(params)) {
            const restoring = hub.removed.find(
              (instance) => instance.instance_id === params.instance_id,
            )
            if (restoring === undefined) return undefined
            hub.removed = hub.removed.filter((instance) => instance !== restoring)
            hub.instances = [
              ...hub.instances,
              { ...restoring, intended_state: "stopped", process: "stopped" },
            ]
            return finished("restore", restoring.instance_id)
          }
          if (method === "instance.delete.preview" && isRecord(params)) {
            const instance_id = String(params.instance_id)
            return {
              instance_id,
              directories: [`/srv/kinby/instances/${instance_id}`],
              volumes: [],
            }
          }
          if (method === "instance.delete" && isRecord(params)) {
            const deleting = hub.removed.find(
              (instance) => instance.instance_id === params.instance_id,
            )
            if (deleting === undefined) return undefined
            hub.removed = hub.removed.filter((instance) => instance !== deleting)
            return finished("delete", deleting.instance_id)
          }
          if (method === "operation.get" && isRecord(params)) {
            return operations.get(String(params.operation_id))
          }
          // Nothing left to set up. A page waits for this read before its empty state.
          if (method === "instance.status") {
            return {
              instance_id: "",
              process: "created",
              readiness: "not-running",
              setup: { logins: [], secrets: [] },
              recreate_reasons: [],
            }
          }
          return undefined
        })
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

/** How a stub answers each method. An answer that throws a `CallError` answers with that error. */
export type Answers = { [M in Method]?: (params: Command<M>) => Result<M> }

export interface StubCaller extends Pick<Client, "call"> {
  /** Every call, oldest first. */
  readonly calls: { method: Method; params: unknown }[]
}

/** A client that answers from `answers` without a hub, and refuses any method it has no answer for. */
export function stubCaller(answers: Answers): StubCaller {
  const calls: StubCaller["calls"] = []
  return {
    calls,
    async call(method, params) {
      calls.push({ method, params })
      const answer = answers[method]
      if (answer === undefined) {
        throw new CallError({
          code: "NOT_FOUND",
          message: `The stub has no answer for ${method}.`,
          retryable: false,
        })
      }
      return answer(params)
    },
  }
}

/** One subscription a stub took, answered by the test. */
export interface StubSubscription {
  readonly method: SubscriptionMethod
  readonly params: unknown
  /** Whether the reader cancelled it. */
  readonly cancelled: boolean
  /** The hub subscribes at `headSequence`. */
  subscribed(headSequence: number): void
  deliver(item: Item<SubscriptionMethod>): void
  /** The hub ends the subscription with an error frame. */
  fail(error: CallError): void
}

export interface StubSubscriber extends Pick<Client, "subscribe"> {
  /** Every subscription, oldest first. */
  readonly subscriptions: StubSubscription[]
}

/** A client whose subscriptions the test answers without a hub. */
export function stubSubscriber(): StubSubscriber {
  const subscriptions: StubSubscription[] = []
  return {
    subscriptions,
    subscribe(method, params) {
      const items: Item<typeof method>[] = []
      let ended = false
      let failure: CallError | undefined
      let wake = () => {}
      let subscribed = (_headSequence: number) => {}
      const head = new Promise<number>((resolve) => (subscribed = resolve))
      const end = (error?: CallError) => {
        ended = true
        failure = error
        wake()
      }
      const subscription = {
        method,
        params,
        cancelled: false,
        subscribed,
        deliver(item: Item<SubscriptionMethod>) {
          items.push(item)
          wake()
        },
        fail: end,
      }
      subscriptions.push(subscription)
      async function* read(): AsyncGenerator<Item<typeof method>, void> {
        for (;;) {
          const next = items.shift()
          if (next !== undefined) yield next
          else if (failure !== undefined) throw failure
          else if (ended) return
          else await new Promise<void>((resolve) => (wake = resolve))
        }
      }
      return Object.assign(read(), {
        head,
        cancel() {
          subscription.cancelled = true
          end()
        },
      })
    },
  }
}

/** Build an instance as `instance.list` reports it, filling in what a test does not care about. */
export function instanceSummary(
  fields: Pick<InstanceSummary, "instance_id"> & Partial<InstanceSummary>,
): InstanceSummary {
  return {
    avatar: { shape: "circle", color: "blue" },
    image_id: "sha256:image",
    intended_state: "running",
    manifest_id: fields.instance_id,
    notices: [],
    persona_name: null,
    process: "running",
    runtime_id: "runtime",
    setup_pending: false,
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
  answer: (method: unknown, params: unknown) => object | undefined,
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
      const result = answer(frame.method, frame.params)
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
