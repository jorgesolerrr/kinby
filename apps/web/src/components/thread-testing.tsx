import type { Event, InstanceSummary, ThreadSummary } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller, stubSubscriber } from "@kinby/contract/testing"
import { render, screen } from "@testing-library/react"

import { ThreadPanel } from "@/components/thread-panel"

// jsdom lays nothing out, so it has no ResizeObserver, and the split beside the thread measures
// with it. The stub stays out of the test setup: with a ResizeObserver, the charts never draw.
window.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

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
 * answers from `hub`, on `clock`, while the hub reports `notices` about the instance.
 */
export function openThread(
  answers: Answers = {},
  client = instanceClient(answers),
  hub = hubClient(),
  clock = fakeClock(),
  notices: InstanceSummary["notices"] = [],
) {
  const panel = (reported: InstanceSummary["notices"]) => (
    <ThreadPanel
      client={client}
      hub={hub}
      clock={clock}
      instanceId="hub-ada"
      threadId="t1"
      name="Ada"
      notices={reported}
    />
  )
  const rendered = render(panel(notices))
  /** List the instance again, now reporting `reported`. */
  const relist = (reported: InstanceSummary["notices"]) => rendered.rerender(panel(reported))
  const subscription = () => {
    const latest = client.subscriptions.at(-1)
    if (latest === undefined) throw new Error("The panel did not subscribe.")
    return latest
  }
  /** What the panel called besides the header listing the threads. */
  const sent = () => client.calls.filter((call) => call.method !== "thread.list")
  return { client, relist, sent, subscription, unmount: rendered.unmount }
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

/** Thread t1 as its instance lists it. */
export function threadSummary(fields: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    id: "t1",
    title: "Deploy notes",
    created_at: "2026-09-27T10:00:00Z",
    last_activity_at: "2026-09-27T10:00:00Z",
    status: "idle",
    mode: "ask",
    mode_pinned: false,
    archived: false,
    origin: { kind: "user" },
    ...fields,
  }
}

/** The line of a tool run, or of a call in an unfolded run, that starts with `words`. */
export function traceLine(words: string): HTMLElement {
  return screen.getByRole("button", { name: (name) => name.startsWith(words) })
}
