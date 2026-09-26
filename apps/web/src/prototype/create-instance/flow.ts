// PROTOTYPE, throwaway. In-memory state for the create-instance flow, shared by every variant.
import { useCallback, useEffect, useRef, useState } from "react"

import { COLORS, FAILURE, packageById, type Shape } from "./stub"

export type Stage =
  | "choosing"
  | "details"
  | "preparing"
  | "failed"
  | "setup"
  | "ready"
  | "starting"
  | "chat"

export const STAGES: Stage[] = [
  "choosing",
  "details",
  "preparing",
  "failed",
  "setup",
  "ready",
  "starting",
  "chat",
]

export type StepState = "pending" | "running" | "succeeded" | "failed"
export type LoginState = "pending" | "waiting" | "done"

export interface Call {
  method: string
  payload: unknown
  // A call the contract does not have yet.
  missing: boolean
}

export interface FlowState {
  stage: Stage
  packageId: string
  name: string
  shape: Shape
  color: string
  values: Record<string, string>
  steps: { name: string; state: StepState }[]
  logins: Record<string, LoginState>
  failNext: boolean
  error: typeof FAILURE | null
  log: Call[]
}

const initial: FlowState = {
  stage: "choosing",
  packageId: "coder",
  name: "",
  shape: "squircle",
  color: COLORS[3],
  values: {},
  steps: [],
  logins: {},
  failNext: false,
  error: null,
  log: [],
}

// Values a jump fills in so a later stage renders as if the user had got there.
function filled(state: FlowState): FlowState {
  const card = packageById(state.packageId)
  const values = { ...state.values }
  for (const field of card.fields) {
    values[field.name] ??=
      field.default ?? (field.kind === "secret" ? "••••••••" : "jorgesolerrr/kinby")
  }
  return { ...state, name: state.name || "Forge", values }
}

export function useFlow() {
  const [state, setState] = useState<FlowState>(initial)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  const patch = useCallback(
    (change: Partial<FlowState>) => setState((s) => ({ ...s, ...change })),
    [],
  )

  const call = useCallback((method: string, payload: unknown, missing = false) => {
    setState((s) => ({ ...s, log: [...s.log, { method, payload, missing }] }))
  }, [])

  /** Walk operation steps one by one, the way polling `operation.get` would show them. */
  const runOperation = useCallback((names: string[], onDone: () => void, failAt?: string) => {
    clearTimeout(timer.current)
    setState((s) => ({
      ...s,
      steps: names.map((name) => ({ name, state: "pending" })),
      error: null,
    }))
    const advance = (index: number) => {
      timer.current = setTimeout(() => {
        const name = names[index]
        if (name === undefined) {
          onDone()
          return
        }
        if (name === failAt) {
          setState((s) => ({
            ...s,
            stage: "failed",
            error: FAILURE,
            failNext: false,
            steps: s.steps.map((step, i) => (i === index ? { ...step, state: "failed" } : step)),
          }))
          return
        }
        setState((s) => ({
          ...s,
          steps: s.steps.map((step, i) =>
            i < index
              ? { ...step, state: "succeeded" }
              : i === index
                ? { ...step, state: "running" }
                : step,
          ),
        }))
        timer.current = setTimeout(() => {
          setState((s) => ({
            ...s,
            steps: s.steps.map((step, i) => (i === index ? { ...step, state: "succeeded" } : step)),
          }))
          advance(index + 1)
        }, 700)
      }, 150)
    }
    advance(0)
  }, [])

  /** Finish a login: pretend the device code was entered in another tab. */
  const login = useCallback(
    (id: string, instanceId: string) => {
      call("instance.setup.login", { instance_id: instanceId, login: id }, true)
      setState((s) => ({ ...s, logins: { ...s.logins, [id]: "waiting" } }))
      timer.current = setTimeout(() => {
        setState((s) => {
          const logins = { ...s.logins, [id]: "done" as const }
          const card = packageById(s.packageId)
          const allDone = card.logins.every((l) => logins[l.id] === "done")
          return { ...s, logins, stage: allDone && s.stage === "setup" ? "ready" : s.stage }
        })
      }, 1800)
    },
    [call],
  )

  const jump = useCallback((stage: Stage) => {
    clearTimeout(timer.current)
    setState((s) => {
      const next = filled({ ...s, stage, error: stage === "failed" ? FAILURE : null })
      const card = packageById(next.packageId)
      if (stage === "ready" || stage === "starting" || stage === "chat") {
        next.logins = Object.fromEntries(card.logins.map((l) => [l.id, "done" as const]))
      }
      if (stage === "setup") next.logins = {}
      if (stage === "choosing") return { ...initial, log: s.log }
      return next
    })
  }, [])

  const reset = useCallback(() => {
    clearTimeout(timer.current)
    setState(initial)
  }, [])

  return { state, patch, call, runOperation, login, jump, reset }
}

export type Flow = ReturnType<typeof useFlow>

export function slug(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "new-instance"
  )
}

/** The command `instance.create` would carry, secrets redacted. */
export function createCommand(state: FlowState, withSecrets: boolean) {
  const card = packageById(state.packageId)
  const secrets = Object.fromEntries(
    card.fields
      .filter((f) => f.kind === "secret" && state.values[f.name])
      .map((f) => [f.name, "<redacted>"]),
  )
  const config = Object.fromEntries(
    card.fields
      .filter((f) => f.kind === "config" && f.name !== "model")
      .map((f) => [f.name, state.values[f.name] ?? f.default ?? ""]),
  )
  return {
    manifest_id: slug(state.name),
    persona_name: state.name || null,
    model: state.values.model ?? "claude-sonnet-5",
    package:
      card.distribution === null
        ? null
        : { id: card.id, distribution: card.distribution, version: card.version },
    // Not in InstanceCreateCommand: the package's declared config fields and the avatar.
    config,
    avatar: { shape: state.shape, color: state.color },
    ...(withSecrets ? { secrets } : {}),
  }
}
