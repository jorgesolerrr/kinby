import type { Contract, ErrorCode, ErrorEnvelope, ErrorFrame, ResultFrame } from "./contract"

type Methods = Contract["methods"]
export type Method = keyof Methods
export type Command<M extends Method> = Methods[M]["command"]
export type Result<M extends Method> = Methods[M]["result"]

/** Where the client stands with the hub. */
export type ConnectionState = "connecting" | "connected" | "disconnected" | "signed-out"

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

export interface Client {
  call: <M extends Method>(method: M, params: Command<M>) => Promise<Result<M>>
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

  constructor({ code, message, retryable }: ErrorEnvelope) {
    super(message)
    this.name = "CallError"
    this.code = code
    this.retryable = retryable
  }
}

interface PendingCall {
  resolve(result: unknown): void
  reject(error: CallError): void
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

/** The app's one connection to the hub whose page it was loaded from. */
export function createClient(origin: string, transport: Transport): Client {
  let state: ConnectionState = "connecting"
  let socket: Socket | undefined
  let nextId = 1
  const pending = new Map<string, PendingCall>()
  const listeners = new Set<() => void>()
  const route = (path: string) => new URL(path, origin).href

  const setState = (next: ConnectionState) => {
    state = next
    for (const listener of listeners) listener()
  }

  // A browser never sees the status of a refused upgrade, so the hub's session route says why.
  const settle = async () => {
    const ended = await transport.fetch(route("/auth/session")).then(
      (response) => response.status === 401,
      () => false,
    )
    if (socket === undefined) setState(ended ? "signed-out" : "disconnected")
  }

  const connect = () => {
    setState("connecting")
    const opened = transport.openSocket(socketUrl(origin), {
      open: () => {
        if (socket === opened) setState("connected")
      },
      message: (data) => {
        const frame = parseReply(data)
        const call = frame === undefined ? undefined : pending.get(frame.id)
        if (frame === undefined || call === undefined) return
        pending.delete(frame.id)
        if (frame.type === "result") call.resolve(frame.result)
        else call.reject(new CallError(frame.error))
      },
      close: () => {
        if (socket !== opened) return
        socket = undefined
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
      if (state !== "connected" || socket === undefined) {
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
    async signIn(token) {
      const login = await transport
        .fetch(route("/auth/login"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        })
        .catch(() => undefined)
      if (!login?.ok) return false
      if (socket === undefined) connect()
      return true
    },
    async signOut() {
      // Whatever the hub answers, the state settles on what its session route says next.
      await transport.fetch(route("/auth/logout"), { method: "POST" }).catch(() => undefined)
      if (socket === undefined) await settle()
      else socket.close()
    },
    state: () => state,
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

/** Read a frame off the socket. Anything that is not a reply the client can route is dropped. */
function parseReply(data: unknown): ((ResultFrame | ErrorFrame) & { id: string }) | undefined {
  if (typeof data !== "string") return undefined
  const frame = parseJson(data)
  if (!isRecord(frame) || typeof frame.id !== "string") return undefined
  if (frame.type === "result" && isRecord(frame.result)) {
    return { type: "result", id: frame.id, result: frame.result }
  }
  if (frame.type === "error" && isErrorEnvelope(frame.error)) {
    return { type: "error", id: frame.id, error: frame.error }
  }
  return undefined
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    isRecord(value) &&
    isErrorCode(value.code) &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean"
  )
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
