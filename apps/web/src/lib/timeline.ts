import type {
  ErrorCode,
  Event,
  GateDecider,
  GateOutcome,
  JsonValue,
  TurnStarted,
  ViewChange,
} from "@kinby/contract"

type Payload = Event["payload"]

/** A thread's turns as the transcript shows them, projected from its events in order. */
export interface Timeline {
  /** The sequence of the last event projected. A subscription asks for what comes after it. */
  sequence: number
  turns: TurnBlock[]
}

export const EMPTY_TIMELINE: Timeline = { sequence: 0, turns: [] }

/** One turn: who started it and when, the request, then its steps in order. */
export interface TurnBlock {
  turnId: string
  startedBy:
    | { kind: "user" }
    | { kind: "routine"; name: string }
    | { kind: "factory-run"; factory: string; step: string }
    | { kind: "home-instance" }
  startedAt: string
  request: string
  steps: Step[]
  /** The approval the turn is parked on, until the turn moves on. */
  approval?: ParkedApproval
  /** How the turn closed. A running turn has none yet. */
  end?: TurnEnd
  /** What the turn's recap left in memory. It runs after the turn, so it can report late or never. */
  recap?: Recap
}

/** An approval the user has not answered, and the tool call it asks about. */
export interface ParkedApproval {
  approvalId: string
  callId: string
  name: string
  arguments: Record<string, JsonValue>
  rule: string
  /** What the approval shows beside the arguments, when the tool says more about its call. */
  preview?: string
}

type Step = TextStep | ToolStep | NotificationStep | ViewStep

interface TextStep {
  kind: "text"
  text: string
}

export interface ToolStep {
  kind: "tool"
  callId: string
  name: string
  arguments: Record<string, JsonValue>
  /** How the gate decided the call, and the user's reason for a denial. An undecided call has none yet. */
  gate?: { action: GateOutcome; decidedBy: GateDecider; rule: string; reason?: string | null }
  /** What the call returned: how long it ran, when the result says, and its output if it failed. */
  result?: { durationMs?: number; error?: string }
}

/** A notification the turn raised on the instance's hub route. */
export interface NotificationStep {
  kind: "notification"
  /** The sequence of its `notification.raised` event, which the hub's answer names. */
  raised: number
  title: string
  text: string
  options: string[]
  /** The hub's answer: the notification it sends, or why it refused it. None while it is pending. */
  hub?: { kind: "accepted"; notificationId: string } | { kind: "refused"; reason: string }
  /**
   * The approval it asks the user to answer: the call it gates, and how it settled, by the gate's
   * decision on the call or by the turn ending first. Unsettled while the turn is parked on it.
   */
  approval?: { callId: string; settled?: GateOutcome | "ended" }
}

/** A view the turn wrote. */
export interface ViewStep {
  kind: "view"
  view: string
  title: string
  change: ViewChange
}

export type TurnEnd =
  | { kind: "done"; tokens: number }
  | { kind: "failed"; code: ErrorCode; message: string }
  | { kind: "stopped" }

export type Recap =
  | { kind: "episode"; node: string }
  | { kind: "none" }
  | { kind: "failed"; message: string }

/** The timeline with one more of the thread's events on it. */
export function project(timeline: Timeline, event: Event): Timeline {
  const { payload } = event
  if (payload.type === "turn.started") {
    const turn: TurnBlock = {
      turnId: event.turn_id,
      startedBy: startedBy(payload.origin),
      startedAt: event.timestamp,
      request: payload.message,
      steps: [],
    }
    return { sequence: event.sequence, turns: [...timeline.turns, turn] }
  }
  return {
    sequence: event.sequence,
    turns: timeline.turns.map((turn) =>
      turn.turnId === event.turn_id ? projectTurn(turn, payload, event.sequence) : turn,
    ),
  }
}

function projectTurn(previous: TurnBlock, payload: Payload, sequence: number): TurnBlock {
  // Answering an approval appends nothing, so the turn's next event is what ends the wait. The
  // notifications recorded while it waits leave it standing.
  const turn = payload.type.startsWith("notification.")
    ? previous
    : { ...previous, approval: undefined }
  switch (payload.type) {
    case "message.delta": {
      const last = turn.steps.at(-1)
      return last?.kind === "text"
        ? {
            ...turn,
            steps: [...turn.steps.slice(0, -1), { ...last, text: last.text + payload.text }],
          }
        : { ...turn, steps: [...turn.steps, { kind: "text", text: payload.text }] }
    }
    case "tool.call":
      return {
        ...turn,
        steps: [
          ...turn.steps,
          {
            kind: "tool",
            callId: payload.call_id,
            name: payload.name,
            arguments: payload.arguments,
          },
        ],
      }
    case "approval.requested": {
      // The request names no call, so it is the undecided call it repeats the name and arguments of.
      const asked = JSON.stringify(payload.arguments)
      const call = turn.steps.find(
        (step): step is ToolStep =>
          step.kind === "tool" &&
          step.gate === undefined &&
          step.name === payload.name &&
          JSON.stringify(step.arguments) === asked,
      )
      if (call === undefined) return turn
      const approval = {
        approvalId: payload.approval_id,
        callId: call.callId,
        name: payload.name,
        arguments: payload.arguments,
        rule: payload.rule,
        ...(payload.preview != null && { preview: payload.preview }),
      }
      return { ...turn, approval }
    }
    case "tool.gated": {
      const gated = updateTool(turn, payload.call_id, (step) => ({
        ...step,
        gate: {
          action: payload.action,
          decidedBy: payload.decided_by,
          rule: payload.rule,
          reason: payload.reason,
        },
      }))
      return settleApprovals(gated, payload.action, payload.call_id)
    }
    case "tool.result":
      return updateTool(turn, payload.call_id, (step) => ({
        ...step,
        result: {
          ...(payload.duration_ms != null && { durationMs: payload.duration_ms }),
          ...(payload.error && { error: payload.output }),
        },
      }))
    case "notification.raised": {
      const { title, text, options = [] } = payload
      const parked = turn.approval
      const approval =
        parked !== undefined && payload.approval?.request.approval_id === parked.approvalId
          ? { callId: parked.callId }
          : undefined
      return {
        ...turn,
        steps: [
          ...turn.steps,
          { kind: "notification", raised: sequence, title, text, options, approval },
        ],
      }
    }
    case "notification.accepted":
      return updateNotification(turn, payload.raised, {
        kind: "accepted",
        notificationId: payload.notification_id,
      })
    case "notification.refused":
      return updateNotification(turn, payload.raised, { kind: "refused", reason: payload.reason })
    case "view.changed": {
      const { view, title, change } = payload
      return { ...turn, steps: [...turn.steps, { kind: "view", view, title, change }] }
    }
    case "turn.completed":
      return settleApprovals(
        { ...turn, end: { kind: "done", tokens: payload.input_tokens + payload.output_tokens } },
        "ended",
      )
    case "turn.failed":
      return settleApprovals(
        { ...turn, end: { kind: "failed", code: payload.code, message: payload.message } },
        "ended",
      )
    case "turn.interrupted":
      return settleApprovals({ ...turn, end: { kind: "stopped" } }, "ended")
    case "memory.recapped":
      return {
        ...turn,
        recap: payload.node === null ? { kind: "none" } : { kind: "episode", node: payload.node },
      }
    case "warning":
      return payload.sources.includes("recap")
        ? { ...turn, recap: { kind: "failed", message: payload.message } }
        : turn
    default:
      return turn
  }
}

function updateTool(
  turn: TurnBlock,
  callId: string,
  update: (step: ToolStep) => ToolStep,
): TurnBlock {
  return {
    ...turn,
    steps: turn.steps.map((step) =>
      step.kind === "tool" && step.callId === callId ? update(step) : step,
    ),
  }
}

function updateNotification(
  turn: TurnBlock,
  raised: number,
  hub: NotificationStep["hub"],
): TurnBlock {
  return {
    ...turn,
    steps: turn.steps.map((step) =>
      step.kind === "notification" && step.raised === raised ? { ...step, hub } : step,
    ),
  }
}

/** The turn with each unsettled approval notification settled, or only the one on `callId`. */
function settleApprovals(
  turn: TurnBlock,
  settled: GateOutcome | "ended",
  callId?: string,
): TurnBlock {
  return {
    ...turn,
    steps: turn.steps.map((step) =>
      step.kind === "notification" &&
      step.approval !== undefined &&
      step.approval.settled === undefined &&
      (callId === undefined || step.approval.callId === callId)
        ? { ...step, approval: { ...step.approval, settled } }
        : step,
    ),
  }
}

function startedBy(origin: TurnStarted["origin"]): TurnBlock["startedBy"] {
  switch (origin?.kind) {
    case "routine":
      return { kind: "routine", name: origin.name }
    case "factory-run":
      return { kind: "factory-run", factory: origin.factory, step: origin.step }
    case "home-instance":
      return { kind: "home-instance" }
    default:
      return { kind: "user" }
  }
}
