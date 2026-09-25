/** Where the client stands with the hub. */
export type ConnectionState = "connecting" | "connected" | "reconnecting" | "signed-out"

export interface Connection {
  readonly state: ConnectionState
  /** Drops since the socket last held. Each one doubles the backoff ceiling. */
  readonly drops: number
}

/** What happened to the connection: the socket opened, held, or dropped, or the session changed. */
export type ConnectionEvent = "opened" | "steady" | "dropped" | "session-ended" | "signed-in"

export const INITIAL_CONNECTION: Connection = { state: "connecting", drops: 0 }

export function project(connection: Connection, event: ConnectionEvent): Connection {
  switch (event) {
    case "opened":
      return { ...connection, state: "connected" }
    case "steady":
      return { ...connection, drops: 0 }
    case "dropped":
      return { state: "reconnecting", drops: connection.drops + 1 }
    case "session-ended":
      return { state: "signed-out", drops: 0 }
    case "signed-in":
      return INITIAL_CONNECTION
  }
}
