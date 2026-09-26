import {
  type ConnectionEvent,
  type ConnectionState,
  INITIAL_CONNECTION,
  project,
} from "./connection"
import type {
  Contract,
  EndFrame,
  ErrorCode,
  ErrorEnvelope,
  ErrorFrame,
  ResultFrame,
} from "./contract"

type Methods = Contract["methods"]
export type Method = keyof Methods
export type Command<M extends Method> = Methods[M]["command"]
export type Result<M extends Method> = Methods[M]["result"]

type Subscriptions = Contract["subscriptions"]
export type SubscriptionMethod = keyof Subscriptions
export type SubscriptionCommand<M extends SubscriptionMethod> = Subscriptions[M]["command"]
export type Item<M extends SubscriptionMethod> = Subscriptions[M]["item"]

/** A subscription's items, in order. They end on the hub's end frame, or on cancel. */
export interface Subscription<M extends SubscriptionMethod> extends AsyncIterable<Item<M>> {
  cancel(): void
}

/** The first backoff ceiling. It doubles with each drop, up to the last. */
const FIRST_BACKOFF_MS = 500
const LAST_BACKOFF_MS = 16_000
/** How long the socket stays up before its drops are forgotten. */
const STEADY_MS = 30_000

export interface SocketEvents {
  open(): void
  message(data: unknown): void
  close(): void
}

export interface Socket {
  send(data: string): void
  close(): void
}

/** How the client reaches the hub: its socket, and the HTTP routes of the browser session. */
export interface Transport {
  openSocket(url: string, events: SocketEvents): Socket
  fetch(url: string, init?: RequestInit): Promise<Response>
}

/** When the client tries again: its timer, and the jitter it draws. */
export interface Clock {
  /** Call back after `ms`. The returned function cancels the call. */
  after(ms: number, callback: () => void): () => void
  /** A draw in [0, 1). */
  random(): number
}

export interface Client {
  call: <M extends Method>(method: M, params: Command<M>) => Promise<Result<M>>
  /** Follow a subscription across reconnects, resuming after the last item it delivered. */
  subscribe: <M extends SubscriptionMethod>(
    method: M,
    params: SubscriptionCommand<M>,
  ) => Subscription<M>
  /** Exchange the access token for a browser session, then connect. False when the hub refuses it. */
  signIn: (token: string) => Promise<boolean>
  signOut: () => Promise<void>
  state: () => ConnectionState
  onStateChange: (listener: () => void) => () => void
}

/** A call the hub answered with an error frame, or one the connection could not carry. */
export class CallError extends Error {
  readonly code: ErrorCode
  readonly retryable: boolean
  /** What is wrong with each value the call sent, by field name. Only INVALID_SETUP fills it. */
  readonly fields: Record<string, string>

  constructor({ code, message, retryable, fields = {} }: ErrorEnvelope) {
    super(message)
    this.name = "CallError"
    this.code = code
    this.retryable = retryable
    this.fields = fields
  }
}

interface PendingCall {
  resolve(result: unknown): void
  reject(error: CallError): void
}

interface OpenSubscription {
  readonly method: SubscriptionMethod
  readonly params: object
  /** The sequence of the last item delivered. A resubscribe asks for what comes after it. */
  lastSequence: number | undefined
  deliver(sequence: number, item: object): void
  end(error?: CallError): void
}

// Never retried: the call may have run. Its outcome shows in the events or the instance status.
const CONNECTION_LOST: ErrorEnvelope = {
  code: "CONNECTION_LOST",
  message: "The connection to the hub dropped before the call returned.",
  retryable: false,
}

const NOT_CONNECTED: ErrorEnvelope = {
  code: "CONNECTION_LOST",
  message: "Not connected to the hub. The call was not sent.",
  retryable: true,
}

/** The browser's own socket and fetch. The session cookie rides on both. */
export const browserTransport: Transport = {
  openSocket(url, events) {
    const socket = new WebSocket(url)
    socket.onopen = () => events.open()
    socket.onmessage = (message) => events.message(message.data)
    socket.onclose = () => events.close()
    return socket
  },
  fetch: (url, init) => fetch(url, init),
}

export const browserClock: Clock = {
  after(ms, callback) {
    const timer = setTimeout(callback, ms)
    return () => clearTimeout(timer)
  },
  random: () => Math.random(),
}

/** The app's one connection to the hub whose page it was loaded from. */
export function createClient(
  origin: string,
  transport: Transport,
  clock: Clock = browserClock,
): Client {
  let connection = INITIAL_CONNECTION
  let socket: Socket | undefined
  let nextId = 1
  const pending = new Map<string, PendingCall>()
  const subscriptions = new Map<string, OpenSubscription>()
  const listeners = new Set<() => void>()
  let settleGeneration = 0
  // Either the wait before the next try, or the one until an open socket counts as steady.
  let cancelTimer = () => {}
  const route = (path: string) => new URL(path, origin).href

  const dispatch = (event: ConnectionEvent) => {
    connection = project(connection, event)
    for (const listener of listeners) listener()
  }

  // A browser never sees the status of a refused upgrade, so the hub's session route says why.
  // A newer check invalidates one already in flight, so a late 204 cannot undo sign-out.
  const settle = async () => {
    const generation = ++settleGeneration
    const ended = await transport.fetch(route("/auth/session")).then(
      (response) => response.status === 401,
      () => false,
    )
    if (generation !== settleGeneration || socket !== undefined) return
    cancelTimer()
    if (ended) return dispatch("session-ended")
    dispatch("dropped")
    // Full jitter: anywhere from no wait to the ceiling, so clients never retry in step.
    const ceiling = Math.min(LAST_BACKOFF_MS, FIRST_BACKOFF_MS * 2 ** (connection.drops - 1))
    cancelTimer = clock.after(clock.random() * ceiling, connect)
  }

  const sendSubscribe = (id: string, { method, params, lastSequence }: OpenSubscription) => {
    const resumed =
      lastSequence === undefined ? params : { ...params, after_sequence: lastSequence }
    socket?.send(JSON.stringify({ type: "subscribe", id, method, params: resumed }))
  }

  const answer = (frame: Reply) => {
    const call = pending.get(frame.id)
    const subscription = subscriptions.get(frame.id)
    switch (frame.type) {
      case "item":
        return subscription?.deliver(frame.sequence, frame.item)
      case "result":
        pending.delete(frame.id)
        return call?.resolve(frame.result)
      case "error":
        pending.delete(frame.id)
        subscriptions.delete(frame.id)
        call?.reject(new CallError(frame.error))
        return subscription?.end(new CallError(frame.error))
      case "end":
        subscriptions.delete(frame.id)
        return subscription?.end()
    }
  }

  const connect = () => {
    cancelTimer()
    const opened = transport.openSocket(socketUrl(origin), {
      open: () => {
        if (socket !== opened) return
        for (const [id, subscription] of subscriptions) sendSubscribe(id, subscription)
        dispatch("opened")
        cancelTimer = clock.after(STEADY_MS, () => dispatch("steady"))
      },
      message: (data) => {
        const frame = parseReply(data)
        if (frame !== undefined) answer(frame)
      },
      close: () => {
        if (socket !== opened) return
        socket = undefined
        cancelTimer()
        for (const call of pending.values()) call.reject(new CallError(CONNECTION_LOST))
        pending.clear()
        void settle()
      },
    })
    socket = opened
  }

  connect()

  return {
    call(method, params) {
      if (connection.state !== "connected" || socket === undefined) {
        return Promise.reject(new CallError(NOT_CONNECTED))
      }
      const id = String(nextId++)
      const sent = socket
      return new Promise((resolve, reject) => {
        pending.set(id, {
          // The generated contract types come from the hub's models, and a test keeps them in step.
          resolve: (result) => resolve(result as Result<typeof method>),
          reject,
        })
        sent.send(JSON.stringify({ type: "call", id, method, params }))
      })
    },
    subscribe(method, params) {
      const id = String(nextId++)
      const stop = () => {
        if (subscriptions.delete(id) && connection.state === "connected") {
          socket?.send(JSON.stringify({ type: "cancel", id }))
        }
      }
      const items = itemQueue<Item<typeof method>>(stop)
      const subscription: OpenSubscription = {
        method,
        params,
        lastSequence: undefined,
        deliver(sequence, item) {
          // A resubscribe can replay what was already delivered.
          if (subscription.lastSequence !== undefined && sequence <= subscription.lastSequence) {
            return
          }
          subscription.lastSequence = sequence
          // The generated contract types come from the hub's models, and a test keeps them in step.
          items.push(item as Item<typeof method>)
        },
        end: items.end,
      }
      subscriptions.set(id, subscription)
      if (connection.state === "connected") sendSubscribe(id, subscription)
      return Object.assign(items.read, {
        cancel: () => {
          stop()
          items.drop()
        },
      })
    },
    async signIn(token) {
      const login = await transport
        .fetch(route("/auth/login"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        })
        .catch(() => undefined)
      if (!login?.ok) return false
      if (socket === undefined) {
        dispatch("signed-in")
        connect()
      }
      return true
    },
    async signOut() {
      // Whatever the hub answers, the state settles on what its session route says next.
      await transport.fetch(route("/auth/logout"), { method: "POST" }).catch(() => undefined)
      if (socket === undefined) await settle()
      else socket.close()
    },
    state: () => connection.state,
    onStateChange: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

function socketUrl(origin: string): string {
  const url = new URL("/ws", origin)
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  return url.href
}

/** Items handed from the socket to one reader, in order, until they end or the reader stops. */
function itemQueue<T>(onStop: () => void) {
  const queued: T[] = []
  let ended = false
  let failure: CallError | undefined
  let wake = () => {}

  async function* read(): AsyncGenerator<T, void> {
    try {
      for (;;) {
        const next = queued.shift()
        if (next !== undefined) yield next
        else if (failure !== undefined) throw failure
        else if (ended) return
        else await new Promise<void>((resolve) => (wake = resolve))
      }
    } finally {
      onStop()
    }
  }

  const end = (error?: CallError) => {
    ended = true
    failure = error
    wake()
  }
  return {
    read: read(),
    push: (item: T) => {
      queued.push(item)
      wake()
    },
    end,
    /** End now, dropping what the reader has not read yet. */
    drop: () => {
      queued.length = 0
      end()
    },
  }
}

type Reply =
  | ((ResultFrame | ErrorFrame | EndFrame) & { id: string })
  | { type: "item"; id: string; sequence: number; item: Record<string, unknown> }

/** Read a frame off the socket. Anything that is not a reply the client can route is dropped. */
function parseReply(data: unknown): Reply | undefined {
  if (typeof data !== "string") return undefined
  const frame = parseJson(data)
  if (!isRecord(frame) || typeof frame.id !== "string") return undefined
  if (frame.type === "result" && isRecord(frame.result)) {
    return { type: "result", id: frame.id, result: frame.result }
  }
  if (frame.type === "error" && isErrorEnvelope(frame.error)) {
    return { type: "error", id: frame.id, error: frame.error }
  }
  // Every subscription's items are events, and the sequence is how a resubscribe resumes.
  if (frame.type === "item" && isRecord(frame.item) && typeof frame.item.sequence === "number") {
    return { type: "item", id: frame.id, sequence: frame.item.sequence, item: frame.item }
  }
  if (frame.type === "end") return { type: "end", id: frame.id }
  return undefined
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    isRecord(value) &&
    isErrorCode(value.code) &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean" &&
    (value.fields === undefined || isTextRecord(value.fields))
  )
}

function isTextRecord(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((text) => typeof text === "string")
}

// A record rather than a list, so the type checker fails when the contract adds or drops a code.
const ERROR_CODES: Record<ErrorCode, true> = {
  NOT_FOUND: true,
  THREAD_BUSY: true,
  INSTANCE_BUSY: true,
  INSTANCE_DRAINING: true,
  TURN_OPEN: true,
  NO_ACTIVE_TURN: true,
  PARKED_TURN_UNAVAILABLE: true,
  PERMISSION_DENIED: true,
  BUDGET_EXCEEDED: true,
  MODEL_UNPRICED: true,
  SNAPSHOT_UNAVAILABLE: true,
  RESOURCE_EXHAUSTED: true,
  INVALID_ARGUMENT: true,
  NOT_PREPARED: true,
  INVALID_SETUP: true,
  CONNECTION_LOST: true,
  INTERNAL: true,
}

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === "string" && Object.hasOwn(ERROR_CODES, value)
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
