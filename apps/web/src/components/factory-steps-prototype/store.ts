// PROTOTYPE (#1450), throwaway. The factory's steps in memory, shared by every variant so a change
// survives a variant switch, and the factory check's rules for turning a step off. Nothing persists:
// a reload starts again from the shipped factory.
import { useSyncExternalStore } from "react"

import { GIVEN, STEPS, type Step, type Target } from "./data"

interface State {
  steps: Step[]
  /** The run's retry after an edit, once the user asked for it. */
  retried: boolean
  /** What the last save changed, so the page can say it. */
  saved?: string
}

let state: State = { steps: STEPS, retried: false }
const listeners = new Set<() => void>()
const set = (next: State) => {
  state = next
  listeners.forEach((l) => l())
}

export function useFactory(): State {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => state,
  )
}

export function saveStep(id: string, draft: Step) {
  set({
    ...state,
    steps: state.steps.map((step) => (step.id === id ? draft : step)),
    saved: `Saved ${draft.id}. Every line on the factory runs it from its next attempt.`,
  })
}

export function addStep(after: string, step: Step) {
  const at = state.steps.findIndex((each) => each.id === after) + 1
  set({
    ...state,
    steps: [...state.steps.slice(0, at), { ...step, added: true }, ...state.steps.slice(at)],
    saved: `Added ${step.id} after ${after}.`,
  })
}

export function setOff(id: string, off: boolean) {
  set({
    ...state,
    steps: state.steps.map((step) => (step.id === id ? { ...step, off } : step)),
    saved: `${id} is ${off ? "off" : "on"}.`,
  })
}

export function deleteStep(id: string) {
  set({ ...state, steps: state.steps.filter((step) => step.id !== id), saved: `Deleted ${id}.` })
}

export function retryRun() {
  set({ ...state, retried: true })
}

export function clearSaved() {
  if (state.saved !== undefined) set({ ...state, saved: undefined })
}

export function resetPrototype() {
  set({ steps: STEPS, retried: false })
}

/** One route between steps: a failure or an outcome that sends the run back, or a resumed session. */
export interface Route {
  from: string
  to: string
  label: string
  max?: number
  kind: "failed" | "outcome" | "resume"
}

export function backOf(target: Target): { back: string; max: number } | undefined {
  return typeof target === "object" ? target : undefined
}

export function routesOf(steps: Step[]): Route[] {
  const routes: Route[] = []
  for (const step of steps) {
    if (step.failed !== undefined) {
      routes.push({
        from: step.id,
        to: step.failed.back,
        label: "failed",
        max: step.failed.max,
        kind: "failed",
      })
    }
    for (const [outcome, target] of Object.entries(step.outcomes ?? {})) {
      const back = backOf(target)
      if (back !== undefined) {
        routes.push({
          from: step.id,
          to: back.back,
          label: outcome,
          max: back.max,
          kind: "outcome",
        })
      }
    }
    if (step.resume !== undefined) {
      routes.push({ from: step.id, to: step.resume, label: "resumes", kind: "resume" })
    }
  }
  return routes
}

/** Why the factory check refuses to turn `id` off, one line per step that blocks it. */
export function blockers(steps: Step[], id: string): { step: string; why: string }[] {
  const on = steps.filter((step) => !step.off && step.id !== id)
  const target = steps.find((step) => step.id === id)
  const found: { step: string; why: string }[] = []
  for (const step of on) {
    if (step.resume === id) found.push({ step: step.id, why: `resumes ${id}'s session` })
    if (step.failed?.back === id)
      found.push({ step: step.id, why: `sends a failure back to ${id}` })
    for (const [outcome, to] of Object.entries(step.outcomes ?? {})) {
      if (backOf(to)?.back === id)
        found.push({ step: step.id, why: `routes ${outcome} back to ${id}` })
    }
  }
  // A value is still there when the factory gives it or another step on before the reader sets it.
  for (const value of Object.keys(target?.results ?? {})) {
    on.forEach((reader, index) => {
      if (!reader.requires?.includes(value) || GIVEN.includes(value)) return
      if (!on.slice(0, index).some((step) => value in (step.results ?? {}))) {
        found.push({ step: reader.id, why: `needs ${value} from it` })
      }
    })
    if (value === "merge_ready") found.push({ step: "done_requires", why: "needs merge_ready" })
  }
  return found
}
