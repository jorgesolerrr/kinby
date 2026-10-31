import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace"
import { mainArgument, type ParkedApproval, type ToolStep, type TurnEnd } from "@/lib/timeline"
import {
  BanIcon,
  CheckIcon,
  CircleAlertIcon,
  ShieldAlertIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react"

/**
 * A stretch of consecutive tool calls in a turn, as one line saying what they did. It unfolds to
 * one line per call.
 */
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
  const waiting = steps.find((step) => step.callId === approval?.callId)
  const running = end === undefined ? steps.find(isRunning) : undefined
  const failed = steps.filter(isFailed).length
  return (
    <Trace>
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine
            status={waiting === undefined && running !== undefined ? "running" : undefined}
          >
            <TraceTitle>
              {waiting !== undefined
                ? `Waiting for you to approve ${waiting.name}`
                : running !== undefined
                  ? doing(running)
                  : did(steps)}
            </TraceTitle>
            {failed > 0 && (
              <TraceDescription variant="destructive">{failed} failed</TraceDescription>
            )}
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <Trace>
            {steps.map((step) => (
              <ToolCall key={step.callId} step={step} waiting={step === waiting} end={end} />
            ))}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}

/** One tool call and how the gate decided it, unfolding to its arguments and its output. */
function ToolCall({
  step,
  waiting,
  end,
}: {
  step: ToolStep
  waiting: boolean
  end: TurnEnd | undefined
}) {
  const { icon: Icon, decision } = gateDecision(step, waiting, end)
  const status = waiting
    ? "pending"
    : isFailed(step)
      ? "error"
      : end === undefined && isRunning(step)
        ? "running"
        : undefined
  return (
    <TraceCollapsible>
      <TraceCollapsibleTrigger>
        <TraceLine status={status}>
          <TraceIcon>
            <Icon />
          </TraceIcon>
          <TraceTitle>
            {[step.name, mainArgument(step.arguments)].filter(Boolean).join(" ")}
          </TraceTitle>
          {/* A long decision wraps on a phone instead of squeezing the call out of its line. */}
          <TraceDescription className="max-w-2/3">{decision}</TraceDescription>
        </TraceLine>
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <div className="flex flex-col gap-2 text-xs">
          <pre className="max-h-64 overflow-y-auto wrap-anywhere whitespace-pre-wrap">
            {JSON.stringify(step.arguments, null, 2)}
          </pre>
          {step.result?.output ? (
            <pre className="max-h-64 overflow-y-auto wrap-anywhere whitespace-pre-wrap text-foreground">
              {step.result.output}
            </pre>
          ) : null}
        </div>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

/** A call the gate has not denied that has not returned. Once its turn ends, it never will. */
function isRunning({ gate, result }: ToolStep): boolean {
  return gate?.action !== "deny" && result === undefined
}

/** A call that ran and returned an error. A denied call returns one too, without running. */
function isFailed({ gate, result }: ToolStep): boolean {
  return gate?.action !== "deny" && result?.error === true
}

/** What a run of calls did, in words: "Searched the web, opened 2 pages". */
function did(steps: ToolStep[]): string {
  const kinds = steps.map(({ name }) => toolKind(name))
  const count = (kind: ToolKind) => kinds.filter((each) => each === kind).length
  const opened = count("open")
  const browsed = count("browser")
  const others = count("other")
  const words = [
    count("memory") > 0 && "recalled what it knows",
    count("search") > 0 && "searched the web",
    opened > 0 && (opened === 1 ? "opened a page" : `opened ${opened} pages`),
    browsed > 0 && (browsed === 1 ? "used the browser once" : `used the browser ${browsed} times`),
  ].filter((part) => part !== false)
  if (others > 0 && words.length === 0) {
    words.push(others === 1 ? `used ${steps[0]?.name}` : `used ${others} tools`)
  } else if (others > 0) {
    words.push(others === 1 ? "1 more tool" : `${others} more tools`)
  }
  const line = words.join(", ")
  return line.charAt(0).toUpperCase() + line.slice(1)
}

/** What a running call is doing, in the present tense. */
function doing({ name, arguments: args }: ToolStep): string {
  const argument = mainArgument(args)
  switch (toolKind(name)) {
    case "memory":
      return "Recalling what it knows"
    case "search":
      return argument === undefined ? "Searching the web" : `Searching “${argument}”`
    case "open":
      return argument === undefined ? "Opening a page" : `Opening ${argument}`
    case "browser":
      return "Using the browser"
    case "other":
      return `Running ${[name, argument].filter(Boolean).join(" ")}`
  }
}

type ToolKind = "memory" | "search" | "open" | "browser" | "other"

function toolKind(name: string): ToolKind {
  if (name.startsWith("memory_")) return "memory"
  if (name === "web_search") return "search"
  if (name === "browser_open") return "open"
  if (name.startsWith("browser_")) return "browser"
  return "other"
}

/**
 * How the gate decided a call and, once it let the call run, how the call went. A call the gate
 * never decided did not run if its turn has ended.
 */
function gateDecision(
  { gate, result }: ToolStep,
  waiting: boolean,
  end: TurnEnd | undefined,
): { icon: LucideIcon; decision: string } {
  // A denied call's output names the rule or carries the user's reason, so the line stays short.
  if (gate?.action === "deny") {
    return {
      icon: BanIcon,
      decision: `denied by ${gate.decidedBy === "policy" ? "policy" : "you"}`,
    }
  }
  if (gate === undefined && result === undefined) {
    if (end !== undefined) return { icon: BanIcon, decision: "not run" }
    return waiting
      ? { icon: ShieldAlertIcon, decision: "waiting for you" }
      : { icon: WrenchIcon, decision: "running" }
  }
  const outcome = callOutcome(result)
  if (gate?.decidedBy === "user") {
    return { icon: CheckIcon, decision: `approved by you · ${outcome}` }
  }
  return { icon: result?.error ? CircleAlertIcon : WrenchIcon, decision: outcome }
}

/** How a call the gate let run went: whether it failed, and how long it took when the result says. */
function callOutcome(result: ToolStep["result"]): string {
  if (result === undefined) return "running"
  const took =
    result.durationMs === undefined ? undefined : `${result.durationMs.toLocaleString("en")} ms`
  if (!result.error) return took ?? "done"
  return took === undefined ? "failed" : `failed after ${took}`
}
