import { mainArgument, type ParkedApproval, type ToolStep, type TurnEnd } from "@/lib/timeline"
import {
  BanIcon,
  CheckIcon,
  CircleAlertIcon,
  ShieldAlertIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react"

/** A stretch of consecutive tool calls in a turn, as one rail of steps. */
export function ToolRun({
  steps,
  approval,
  end,
}: {
  steps: ToolStep[]
  /** The approval the turn is parked on. */
  approval: ParkedApproval | undefined
  end: TurnEnd | undefined
}) {
  return (
    <ol className="flex flex-col">
      {steps.map((step, index) => (
        <ToolStepRow
          key={step.callId}
          step={step}
          waiting={approval?.callId === step.callId}
          end={end}
          last={index === steps.length - 1}
        />
      ))}
    </ol>
  )
}

/** One tool call on the rail: a node with how the gate decided it, then the call and its outcome. */
function ToolStepRow({
  step,
  waiting,
  end,
  last,
}: {
  step: ToolStep
  waiting: boolean
  end: TurnEnd | undefined
  last: boolean
}) {
  const { icon: Icon, decision } = gateDecision(step, waiting, end)
  const call = [step.name, mainArgument(step.arguments)].filter(Boolean).join(" ")
  const tone = waiting
    ? "bg-ember text-on-ember"
    : step.result?.error === undefined
      ? "bg-muted text-muted-foreground"
      : "bg-destructive/10 text-destructive"
  return (
    <li className="flex gap-3">
      <span className="relative flex w-5 shrink-0 justify-center">
        {!last && <span aria-hidden className="absolute top-6 bottom-0 w-px bg-border" />}
        <span aria-hidden className={`mt-0.5 grid size-5 place-items-center rounded-md ${tone}`}>
          <Icon className="size-3" />
        </span>
      </span>
      <span className="min-w-0 flex-1 pb-3 font-mono text-xs leading-6 wrap-break-word">
        {`${call} · ${decision}`}
      </span>
    </li>
  )
}

/**
 * How the gate decided a call and, once it let the call run, what the call returned. A call the
 * gate never decided did not run if its turn has ended.
 */
function gateDecision(
  { gate, result }: ToolStep,
  waiting: boolean,
  end: TurnEnd | undefined,
): { icon: LucideIcon; decision: string } {
  if (gate?.action === "deny") {
    const decision =
      gate.decidedBy === "policy"
        ? `denied by policy: ${gate.rule}`
        : gate.reason
          ? `denied by you: ${gate.reason}`
          : "denied by you"
    return { icon: BanIcon, decision }
  }
  if (gate === undefined && result === undefined) {
    if (end !== undefined) return { icon: BanIcon, decision: `not run, turn ${end.kind}` }
    return waiting
      ? { icon: ShieldAlertIcon, decision: "waiting for you" }
      : { icon: WrenchIcon, decision: "running" }
  }
  const outcome = callOutcome(result)
  if (gate?.decidedBy === "user") {
    return { icon: CheckIcon, decision: `approved by you · ${outcome}` }
  }
  return { icon: result?.error === undefined ? WrenchIcon : CircleAlertIcon, decision: outcome }
}

/** What a call the gate let run returned: how long it took when the result says, and its error. */
function callOutcome(result: ToolStep["result"]): string {
  if (result === undefined) return "running"
  const took =
    result.durationMs === undefined ? undefined : `${result.durationMs.toLocaleString("en")} ms`
  if (result.error === undefined) return took ?? "done"
  return `${took === undefined ? "failed" : `failed after ${took}`}: ${result.error}`
}
