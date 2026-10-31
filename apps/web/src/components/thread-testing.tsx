import type { Event } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { render } from "@testing-library/react"

import { ThreadPanel } from "@/components/thread-panel"

export type Payload = Event["payload"]

/** A client of an instance whose socket is up, answering from `answers`. */
export function instanceClient(answers: Answers = {}) {
  return {
    ...stubCaller(answers),
    ...stubSubscriber(),
    state: () => "connected" as const,
    onStateChange: () => () => {},
  }
}

/** A client of the hub, answering from `answers`. */
export function hubClient(answers: Answers = {}) {
  return { ...stubCaller(answers), ...stubSubscriber() }
}

/**
 * Open thread t1 of Ada's instance, from a client that answers from `answers`, with a hub that
 * answers from `hub`, on `clock`.
 */
export function openThread(
  answers: Answers = {},
  client = instanceClient(answers),
  hub = hubClient(),
  clock = fakeClock(),
) {
  const rendered = render(
    <ThreadPanel
      client={client}
      hub={hub}
      clock={clock}
      instanceId="hub-ada"
      threadId="t1"
      name="Ada"
      avatar={{ shape: "circle", color: "blue" }}
    />,
  )
  const subscription = () => {
    const latest = client.subscriptions.at(-1)
    if (latest === undefined) throw new Error("The panel did not subscribe.")
    return latest
  }
  /** What the panel called besides the header listing the threads. */
  const sent = () => client.calls.filter((call) => call.method !== "thread.list")
  return { client, sent, subscription, unmount: rendered.unmount }
}

/** The thread's events, numbered from 1 in the order given. */
export function thread(...events: [turnId: string, payload: Payload][]): Event[] {
  return events.map(([turnId, payload], index) => ({
    sequence: index + 1,
    thread_id: "t1",
    turn_id: turnId,
    timestamp: "2026-09-28T10:00:00Z",
    payload,
  }))
}

export const started = (message: string): Payload => ({
  type: "turn.started",
  message,
  model: "claude",
  origin: { kind: "user" },
})
