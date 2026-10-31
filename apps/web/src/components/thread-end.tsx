import type { InstanceClient, TurnVerdict } from "@kinby/contract"
import { cn } from "cn"
import { type ReactNode, useState } from "react"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Spinner } from "@/components/ui/spinner"
import { Toggle } from "@/components/ui/toggle"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { reason } from "@/lib/operation"
import { nodePath, openNode } from "@/lib/selection"
import { duration, tokens } from "@/lib/stats"
import type { Recap, TurnBlock, TurnEnd as End } from "@/lib/timeline"
import {
  CheckIcon,
  CircleAlertIcon,
  CircleStopIcon,
  CopyIcon,
  RefreshCwIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/**
 * How a closed turn ends: a finished one in a row of actions and counts, a failed one in an alert,
 * a stopped one in a line. Each ends with what its recap left. A running turn has no end yet.
 */
export function TurnEnd({
  turn,
  last,
  client,
  threadId,
  instanceId,
}: {
  turn: TurnBlock
  /** Whether it is the thread's latest turn, whose row always shows. */
  last: boolean
  client: Caller
  threadId: string
  instanceId: string
}) {
  const { end, recap } = turn
  const recapped = recap && <RecapOutcome recap={recap} instanceId={instanceId} />
  switch (end?.kind) {
    case undefined:
      return null
    case "done":
      return (
        <EndRow turn={turn} end={end} last={last} client={client} threadId={threadId}>
          {recapped}
        </EndRow>
      )
    case "failed":
      return (
        <div className="flex flex-col gap-2">
          <FailedTurn turn={turn} end={end} client={client} threadId={threadId} />
          {recapped && <div className="text-xs text-muted-foreground">{recapped}</div>}
        </div>
      )
    case "stopped":
      return (
        <Marker>
          <MarkerIcon>
            <CircleStopIcon />
          </MarkerIcon>
          <MarkerContent>
            Stopped
            {recapped && <span className="ml-3">{recapped}</span>}
          </MarkerContent>
        </Marker>
      )
  }
}

/**
 * A finished turn's quiet row: Copy, the rating, its steps, tokens and time, and its recap. It
 * shows on hover, while it holds focus or a popup, and always on the latest turn.
 */
function EndRow({
  turn,
  end,
  last,
  client,
  threadId,
  children,
}: {
  turn: TurnBlock
  end: Extract<End, { kind: "done" }>
  last: boolean
  client: Caller
  threadId: string
  children: ReactNode
}) {
  const [failure, setFailure] = useState<string>()
  const rate = async (verdict: TurnVerdict, why?: string) => {
    try {
      await client.call("thread.turn.rate", {
        thread_id: threadId,
        turn_id: turn.turnId,
        verdict,
        ...(why && { reason: why }),
      })
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    }
  }
  const reply = turn.steps.flatMap((step) => (step.kind === "text" ? [step.text] : [])).join("\n\n")
  const calls = turn.steps.filter((step) => step.kind === "tool").length
  return (
    <div
      className={cn(
        "-mt-2 flex flex-wrap items-center gap-x-0.5 gap-y-1 text-xs text-muted-foreground transition-opacity group-hover/turn:opacity-100 focus-within:opacity-100 has-data-popup-open:opacity-100",
        !last && "opacity-0",
      )}
    >
      <CopyReply reply={reply} />
      <Toggle
        size="sm"
        aria-label="Good turn"
        pressed={turn.rating === "good"}
        onPressedChange={() => void rate("good")}
      >
        <ThumbsUpIcon />
      </Toggle>
      <BadTurn pressed={turn.rating === "bad"} onRate={(why) => rate("bad", why)} />
      <span className="ml-2 tabular-nums">
        {calls} {calls === 1 ? "step" : "steps"} · {tokens(end.tokens)} tokens ·{" "}
        {duration(end.durationMs)}
      </span>
      {children && <span className="ml-3">{children}</span>}
      {failure !== undefined && <span className="ml-3 text-destructive">{failure}</span>}
    </div>
  )
}

function CopyReply({ reply }: { reply: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(reply)
    setCopied(true)
    setTimeout(() => setCopied(false), 2_000)
  }
  return (
    <Button variant="ghost" size="icon-sm" aria-label="Copy" onClick={() => void copy()}>
      {copied ? <CheckIcon /> : <CopyIcon />}
    </Button>
  )
}

/** Thumbs down, which asks why before it rates. The reason is optional. */
function BadTurn({
  pressed,
  onRate,
}: {
  pressed: boolean
  onRate: (why: string) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [why, setWhy] = useState("")
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Toggle size="sm" aria-label="Bad turn" pressed={pressed} />}>
        <ThumbsDownIcon />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64">
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            setOpen(false)
            setWhy("")
            void onRate(why.trim())
          }}
        >
          <Input
            aria-label="What went wrong?"
            placeholder="What went wrong? (optional)"
            value={why}
            onChange={(event) => setWhy(event.target.value)}
          />
          <Button type="submit" size="sm">
            Rate
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  )
}

/** A failed turn's message and code, with Retry on a turn the user started. */
function FailedTurn({
  turn,
  end,
  client,
  threadId,
}: {
  turn: TurnBlock
  end: Extract<End, { kind: "failed" }>
  client: Caller
  threadId: string
}) {
  const [retrying, setRetrying] = useState(false)
  const [failure, setFailure] = useState<string>()
  const retry = async () => {
    setRetrying(true)
    try {
      await client.call("thread.turn.start", { thread_id: threadId, message: turn.request })
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    }
    setRetrying(false)
  }
  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertTitle className="wrap-anywhere">{end.message}</AlertTitle>
      <AlertDescription>
        <p className="font-mono text-xs">{end.code}</p>
        {failure !== undefined && <p>{failure}</p>}
      </AlertDescription>
      {turn.startedBy.kind === "user" && (
        <AlertAction>
          <Button size="xs" variant="outline" disabled={retrying} onClick={() => void retry()}>
            {retrying ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <RefreshCwIcon data-icon="inline-start" />
            )}
            Retry
          </Button>
        </AlertAction>
      )}
    </Alert>
  )
}

/** What the turn's recap left in memory: a link to the episode it kept, nothing, or a failure. */
function RecapOutcome({ recap, instanceId }: { recap: Recap; instanceId: string }) {
  switch (recap.kind) {
    case "episode":
      return (
        <a
          className="font-medium text-foreground underline-offset-3 hover:underline"
          href={nodePath(instanceId, recap.node)}
          onClick={(event) => {
            event.preventDefault()
            openNode(instanceId, recap.node)
          }}
        >
          Recapped
        </a>
      )
    case "none":
      return <span>Nothing to recap</span>
    case "failed":
      return (
        <span className="text-destructive">
          <Tooltip>
            <TooltipTrigger>Recap failed</TooltipTrigger>
            <TooltipContent>{recap.message}</TooltipContent>
          </Tooltip>
        </span>
      )
  }
}
