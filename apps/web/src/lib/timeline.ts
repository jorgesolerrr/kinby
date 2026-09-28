import type { ErrorCode, Event, GateDecider, GateOutcome, JsonValue } from "@kinby/contract"

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
  startedBy: { kind: "user" } | { kind: "routine"; name: string }
  startedAt: string
  request: string
  steps: Step[]
  /** The approval the turn is parked on, until the turn moves on. */
  approval?: ParkedApproval
  /** How the turn closed. A running turn has none yet. */
  end?: TurnEnd
}

/** An approval the user has not answered, and the tool call it asks about. */
interface ParkedApproval {
  approvalId: string
  callId: string
  rule: string
}

type Step = TextStep | ToolStep

interface TextStep {
  kind: "text"
  text: string
}

export interface ToolStep {
  kind: "tool"
  callId: string
  name: string
  arguments: Record<string, JsonValue>
  /** How the gate decided the call. An undecided call has none yet. */
  gate?: { action: GateOutcome; decidedBy: GateDecider; rule: string }
  /** How long the call ran, once it returned. */
  durationMs?: number
}

type TurnEnd =
  | { kind: "done"; tokens: number }
  | { kind: "failed"; code: ErrorCode; message: string }
  | { kind: "stopped" }

/** The timeline with one more of the thread's events on it. */
export function project(timeline: Timeline, event: Event): Timeline {
  const { payload } = event
  if (payload.type === "turn.started") {
    const turn: TurnBlock = {
      turnId: event.turn_id,
      startedBy:
        payload.origin?.kind === "routine"
          ? { kind: "routine", name: payload.origin.name }
          : { kind: "user" },
      startedAt: event.timestamp,
      request: payload.message,
      steps: [],
    }
    return { sequence: event.sequence, turns: [...timeline.turns, turn] }
  }
  return {
    sequence: event.sequence,
    turns: timeline.turns.map((turn) =>
      turn.turnId === event.turn_id ? projectTurn(turn, payload) : turn,
    ),
  }
}

function projectTurn(previous: TurnBlock, payload: Payload): TurnBlock {
  // Answering an approval appends nothing, so the turn's next event is what ends the wait.
  const turn = { ...previous, approval: undefined }
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
      const approval = { approvalId: payload.approval_id, callId: call.callId, rule: payload.rule }
      return { ...turn, approval }
    }
    case "tool.gated":
      return updateTool(turn, payload.call_id, (step) => ({
        ...step,
        gate: { action: payload.action, decidedBy: payload.decided_by, rule: payload.rule },
      }))
    case "tool.result":
      return updateTool(turn, payload.call_id, (step) =>
        payload.duration_ms == null ? step : { ...step, durationMs: payload.duration_ms },
      )
    case "turn.completed":
      return {
        ...turn,
        end: { kind: "done", tokens: payload.input_tokens + payload.output_tokens },
      }
    case "turn.failed":
      return { ...turn, end: { kind: "failed", code: payload.code, message: payload.message } }
    case "turn.interrupted":
      return { ...turn, end: { kind: "stopped" } }
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
