import type { Socket, SocketEvents, Transport } from "./client"

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
}

/** A hub that answers like the real one: the socket upgrades only while the browser session is open. */
export function fakeHub({ signedIn = true } = {}): FakeHub {
  const hub: FakeHub = {
    signedIn,
    reachable: true,
    sockets: [],
    transport: {
      openSocket(url, events) {
        const socket = fakeSocket(url, events)
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

function fakeSocket(url: string, events: SocketEvents): FakeSocket & { accept(): void } {
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    events.close()
  }
  return {
    url,
    sent: [],
    send(data) {
      this.sent.push(JSON.parse(data))
    },
    close,
    receive: (frame) => events.message(typeof frame === "string" ? frame : JSON.stringify(frame)),
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
