// PROTOTYPE, throwaway. A fake instance behind `thread.subscribe`: per-thread event logs using the
// contract's own `Event` type, a replay that ends at `head_sequence`, scripted live turns that
// stream `message.delta`, park on `approval.requested`, and stop on `thread.turn.interrupt`.
// Every call the panel would make lands in `log`; calls the contract does not have are marked.
import type { Event, PermissionMode } from "@kinby/contract"
import { useEffect, useRef, useState } from "react"

import { INSTANCE, seedThreads, type StubThread } from "./stub"

type Payload = Event["payload"]

export type CallEntry = { method: string; params: unknown; missing?: string }

// ---------- the projection the panel renders ----------

export type ToolStep = {
  kind: "tool"
  callId: string
  name: string
  args: Record<string, unknown>
  write: boolean
  approval?: { approvalId: string; rule: string }
  gate?: { action: "allow" | "deny"; decidedBy: "policy" | "user"; rule: string }
  result?: { output: string; error: boolean; durationMs?: number | null }
}
export type TextStep = { kind: "text"; text: string }
export type Step = ToolStep | TextStep

export type TurnView = {
  turnId: string
  message: string
  origin: { kind: "user" } | { kind: "routine"; name: string; trigger: string }
  steps: Step[]
  end?:
    | { kind: "completed"; tokens: number; outcome?: string }
    | { kind: "failed"; code: string; message: string }
    | { kind: "interrupted" }
  startedAt: string
}

export type ThreadView = {
  turns: TurnView[]
  /** The last `mode.pinned`, or undefined when the thread never pinned one. */
  pinnedMode?: PermissionMode
  pending?: { turnId: string; step: ToolStep }
  running: boolean
}

export function project(events: Event[]): ThreadView {
  const turns: TurnView[] = []
  let pinnedMode: PermissionMode | undefined
  const byId = new Map<string, TurnView>()
  for (const event of events) {
    const p = event.payload
    if (p.type === "mode.pinned") {
      pinnedMode = p.mode
      continue
    }
    if (p.type === "turn.started") {
      const origin =
        p.origin?.kind === "routine"
          ? { kind: "routine" as const, name: p.origin.name, trigger: p.origin.trigger }
          : { kind: "user" as const }
      const turn: TurnView = {
        turnId: event.turn_id,
        message: p.message,
        origin,
        steps: [],
        startedAt: event.timestamp,
      }
      turns.push(turn)
      byId.set(event.turn_id, turn)
      continue
    }
    const turn = byId.get(event.turn_id)
    if (turn === undefined) continue
    const last = turn.steps.at(-1)
    const tool = (callId: string) =>
      turn.steps.find((s): s is ToolStep => s.kind === "tool" && s.callId === callId)
    switch (p.type) {
      case "message.delta":
        if (last?.kind === "text") last.text += p.text
        else turn.steps.push({ kind: "text", text: p.text })
        break
      case "tool.call":
        turn.steps.push({
          kind: "tool",
          callId: p.call_id,
          name: p.name,
          args: p.arguments,
          write: p.write === true,
        })
        break
      case "approval.requested": {
        // The event names no call id; the parked call is the last tool call without a gate.
        const parked = [...turn.steps]
          .reverse()
          .find((s): s is ToolStep => s.kind === "tool" && s.gate === undefined)
        if (parked) parked.approval = { approvalId: p.approval_id, rule: p.rule }
        break
      }
      case "tool.gated": {
        const step = tool(p.call_id)
        if (step) step.gate = { action: p.action, decidedBy: p.decided_by, rule: p.rule }
        break
      }
      case "tool.result": {
        const step = tool(p.call_id)
        if (step)
          step.result = { output: p.output, error: p.error, durationMs: p.duration_ms }
        break
      }
      case "turn.completed":
        turn.end = {
          kind: "completed",
          tokens: p.input_tokens + p.output_tokens,
          outcome: p.outcome,
        }
        break
      case "turn.failed":
        turn.end = { kind: "failed", code: p.code, message: p.message }
        break
      case "turn.interrupted":
        turn.end = { kind: "interrupted" }
        break
    }
  }
  const open = turns.at(-1)
  const parked =
    open && !open.end
      ? open.steps.find(
          (s): s is ToolStep => s.kind === "tool" && s.approval !== undefined && !s.gate,
        )
      : undefined
  return {
    turns,
    pinnedMode,
    running: open !== undefined && open.end === undefined,
    pending: parked && open ? { turnId: open.turnId, step: parked } : undefined,
  }
}

// ---------- the fake instance ----------

export type Subscription = {
  threadId: string
  /** Undefined until `subscribed` arrives. */
  headSequence?: number
  /** Items at or below `headSequence` are replay; the panel shows a loading transcript until then. */
  replayed: boolean
}

export type Chat = ReturnType<typeof useChat>

export function useChat() {
  const [threads, setThreads] = useState<StubThread[]>(seedThreads)
  const [selected, setSelected] = useState<string>(() => seedThreads()[0].id)
  const [sub, setSub] = useState<Subscription>({ threadId: selected, replayed: false })
  const [log, setLog] = useState<CallEntry[]>([])
  const [slowReplay, setSlowReplay] = useState(false)
  const timers = useRef<number[]>([])
  const threadsRef = useRef(threads)
  threadsRef.current = threads

  const call = (method: string, params: unknown, missing?: string) =>
    setLog((l) => [...l, { method, params, missing }])

  const append = (threadId: string, turnId: string, payload: Payload) =>
    setThreads((all) =>
      all.map((t) => {
        if (t.id !== threadId) return t
        const sequence = (t.events.at(-1)?.sequence ?? 0) + 1
        const event: Event = {
          sequence,
          thread_id: threadId,
          turn_id: turnId,
          timestamp: new Date().toISOString(),
          payload,
        }
        return { ...t, events: [...t.events, event] }
      }),
    )

  const later = (ms: number, fn: () => void) => {
    timers.current.push(window.setTimeout(fn, ms))
  }
  const stopScripts = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }

  // Subscribing: `subscribed {head_sequence}` first, the replay, then live.
  useEffect(() => {
    setSub({ threadId: selected, replayed: false })
    const head = threadsRef.current.find((t) => t.id === selected)?.events.at(-1)?.sequence ?? 0
    setLog((l) => [
      ...l,
      { method: "subscribe thread.subscribe", params: { thread_id: selected } },
    ])
    const a = window.setTimeout(() => setSub({ threadId: selected, headSequence: head, replayed: false }), 150)
    const b = window.setTimeout(
      () => setSub({ threadId: selected, headSequence: head, replayed: true }),
      slowReplay ? 2500 : 500,
    )
    return () => {
      clearTimeout(a)
      clearTimeout(b)
    }
  }, [selected, slowReplay])

  const thread = threads.find((t) => t.id === selected)!
  const view = project(thread.events)
  const mode: PermissionMode = view.pinnedMode ?? INSTANCE.defaultMode

  const stream = (threadId: string, turnId: string, text: string, start: number) => {
    const words = text.split(/(?<= )/)
    words.forEach((w, i) =>
      later(start + i * 45, () => append(threadId, turnId, { type: "message.delta", text: w })),
    )
    return start + words.length * 45
  }

  const finish = (threadId: string, turnId: string, at: number) => {
    later(at, () =>
      append(threadId, turnId, {
        type: "model.completed",
        model: INSTANCE.model,
        input_tokens: 1840,
        output_tokens: 212,
        duration_ms: 2100,
      }),
    )
    later(at + 50, () =>
      append(threadId, turnId, {
        type: "turn.completed",
        input_tokens: 3920,
        output_tokens: 488,
        outcome: "work",
      }),
    )
  }

  const startTurn = (message: string, attachments: string[] = []) => {
    const threadId = selected
    const turnId = crypto.randomUUID()
    call("thread.turn.start", { thread_id: threadId, message })
    if (attachments.length > 0)
      call(
        "thread.turn.start attachments",
        { attachments },
        "ThreadTurnStartCommand has message only; no attachments",
      )
    append(threadId, turnId, {
      type: "turn.started",
      message,
      model: INSTANCE.model,
      permission_mode: mode,
      origin: { kind: "user" },
    })
    let t = stream(threadId, turnId, "Let me look at the repository first. ", 700)
    const read = crypto.randomUUID()
    later(t, () =>
      append(threadId, turnId, {
        type: "tool.call",
        call_id: read,
        name: "read_file",
        arguments: { path: "src/kinby/hub/runtime.py" },
        write: false,
      }),
    )
    later(t + 300, () =>
      append(threadId, turnId, {
        type: "tool.gated",
        call_id: read,
        name: "read_file",
        action: "allow",
        decided_by: "policy",
        rule: "reads are allowed",
      }),
    )
    later(t + 700, () =>
      append(threadId, turnId, {
        type: "tool.result",
        call_id: read,
        name: "read_file",
        error: false,
        output: "class DockerRuntime:\n    async def create(self, spec: InstanceSpec) -> None: ...",
        duration_ms: 38,
      }),
    )
    t = stream(threadId, turnId, "The fix is a one-line change. I'll write it now. ", t + 900)
    const write = crypto.randomUUID()
    later(t, () =>
      append(threadId, turnId, {
        type: "tool.call",
        call_id: write,
        name: "write_file",
        arguments: { path: "src/kinby/hub/runtime.py", content: "…42 lines…" },
        write: true,
      }),
    )
    if (mode === "ask") {
      later(t + 300, () =>
        append(threadId, turnId, {
          type: "approval.requested",
          approval_id: crypto.randomUUID(),
          name: "write_file",
          arguments: { path: "src/kinby/hub/runtime.py", content: "…42 lines…" },
          rule: "ask mode: writes need approval",
        }),
      )
      return
    }
    const denied = mode === "read-only"
    later(t + 300, () =>
      append(threadId, turnId, {
        type: "tool.gated",
        call_id: write,
        name: "write_file",
        action: denied ? "deny" : "allow",
        decided_by: "policy",
        rule: denied ? "read-only mode: writes are denied" : `${mode} mode: writes are allowed`,
      }),
    )
    afterWrite(threadId, turnId, write, !denied, t + 600)
  }

  const afterWrite = (
    threadId: string,
    turnId: string,
    callId: string,
    allowed: boolean,
    at: number,
  ) => {
    later(at, () =>
      append(threadId, turnId, {
        type: "tool.result",
        call_id: callId,
        name: "write_file",
        error: !allowed,
        output: allowed ? "wrote 42 lines" : "The gate denied this call.",
        duration_ms: allowed ? 12 : null,
      }),
    )
    const t = stream(
      threadId,
      turnId,
      allowed
        ? "Done. The runtime now waits for the container to report healthy before returning."
        : "I couldn't write the file, so here is the change as a patch you can apply yourself.",
      at + 300,
    )
    finish(threadId, turnId, t)
  }

  const respond = (answer: "yes" | "no", note?: string) => {
    const pending = view.pending
    if (!pending) return
    call("thread.approval.respond", {
      thread_id: selected,
      approval_id: pending.step.approval!.approvalId,
      answer,
    })
    if (note)
      call(
        "thread.approval.respond note",
        { note },
        "the answer is only yes / not-yes; a denial's reason never reaches the model",
      )
    const allowed = answer === "yes"
    append(selected, pending.turnId, {
      type: "tool.gated",
      call_id: pending.step.callId,
      name: pending.step.name,
      action: allowed ? "allow" : "deny",
      decided_by: "user",
      rule: pending.step.approval!.rule,
    })
    afterWrite(selected, pending.turnId, pending.step.callId, allowed, 300)
  }

  const interrupt = () => {
    const open = view.turns.at(-1)
    if (!open || open.end) return
    call("thread.turn.interrupt", { thread_id: selected })
    stopScripts()
    later(250, () =>
      append(selected, open.turnId, { type: "turn.interrupted", input_tokens: 900, output_tokens: 40 }),
    )
  }

  const setMode = (next: PermissionMode) => {
    call("thread.mode.set", { thread_id: selected, mode: next })
    if (!INSTANCE.allowedModes.includes(next))
      call("thread.mode.set", { mode: next }, "no call tells the panel the instance's ceiling")
    append(selected, crypto.randomUUID(), { type: "mode.pinned", mode: next })
  }

  const createThread = () => {
    const id = crypto.randomUUID()
    call("thread.create", { title: null })
    setThreads((all) => [
      { id, title: null, createdAt: new Date().toISOString(), events: [] },
      ...all,
    ])
    setSelected(id)
  }

  const select = (id: string) => {
    if (id === selected) return
    // Switching threads ends the old subscription; the live script keeps running on the instance.
    call("cancel", { subscription: sub.threadId })
    setSelected(id)
  }

  const reset = () => {
    stopScripts()
    const fresh = seedThreads()
    setThreads(fresh)
    setSelected(fresh[0].id)
    setLog([])
  }

  return {
    threads,
    thread,
    view,
    mode,
    sub,
    log,
    slowReplay,
    setSlowReplay,
    call,
    select,
    startTurn,
    respond,
    interrupt,
    setMode,
    createThread,
    reset,
  }
}

/** What a thread list row can say, and what it cannot without subscribing to every thread. */
export function threadStatus(t: StubThread): "running" | "approval" | "failed" | "idle" {
  const v = project(t.events)
  if (v.pending) return "approval"
  if (v.running) return "running"
  if (v.turns.at(-1)?.end?.kind === "failed") return "failed"
  return "idle"
}

export function threadTitle(t: StubThread): string {
  return t.title ?? project(t.events).turns[0]?.message.slice(0, 40) ?? "New thread"
}
