import type { InstanceClient, JsonValue } from "@kinby/contract"
import { type ReactNode, useEffect, useState, useSyncExternalStore } from "react"

import { ThreadHeader } from "@/components/thread-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Field, FieldError } from "@/components/ui/field"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Message, MessageContent, MessageHeader } from "@/components/ui/message"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { reason } from "@/lib/operation"
import { threadStore } from "@/lib/thread-store"
import type { ToolStep, TurnBlock } from "@/lib/timeline"
import {
  ArrowUpIcon,
  BanIcon,
  CheckIcon,
  CircleAlertIcon,
  CircleStopIcon,
  RepeatIcon,
  ShieldAlertIcon,
  SquareIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react"

/** A thread's turns as a timeline, followed live once its history has loaded. */
export function ThreadPanel({
  client,
  threadId,
  name,
}: {
  client: Pick<InstanceClient, "call" | "subscribe">
  threadId: string
  /** What the instance is called. */
  name: string
}) {
  const store = threadStore(client, threadId)
  useEffect(() => store.follow(), [store])
  const { timeline, replayed, failure } = useSyncExternalStore(store.onChange, store.view)
  const latest = timeline.turns.at(-1)

  if (failure !== undefined) {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>The thread did not load</AlertTitle>
          <AlertDescription>{failure}</AlertDescription>
        </Alert>
      </div>
    )
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ThreadHeader client={client} threadId={threadId} />
      {/* Half a history must not pass for all of it. */}
      {replayed ? (
        <>
          <Transcript turns={timeline.turns} name={name} />
          <div className="mx-auto w-full max-w-3xl p-4">
            <Composer
              client={client}
              threadId={threadId}
              name={name}
              running={latest !== undefined && latest.end === undefined}
            />
          </div>
        </>
      ) : (
        <TranscriptSkeleton />
      )}
    </div>
  )
}

/**
 * Enter sends the message as a new turn, and Shift+Enter starts a new line. While a turn runs,
 * Send becomes Stop.
 */
function Composer({
  client,
  threadId,
  name,
  running,
}: {
  client: Pick<InstanceClient, "call">
  threadId: string
  name: string
  running: boolean
}) {
  const [message, setMessage] = useState("")
  const [sending, setSending] = useState(false)
  const [failure, setFailure] = useState<string>()
  const text = message.trim()

  const send = async () => {
    if (text === "" || sending || running) return
    setSending(true)
    try {
      await client.call("thread.turn.start", { thread_id: threadId, message: text })
      setMessage("")
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    } finally {
      setSending(false)
    }
  }
  const stop = () =>
    client.call("thread.turn.interrupt", { thread_id: threadId }).then(
      () => setFailure(undefined),
      (error: unknown) => setFailure(reason(error)),
    )

  return (
    <Field data-invalid={failure !== undefined || undefined}>
      <Textarea
        aria-label={`Message ${name}`}
        placeholder={`Message ${name}`}
        value={message}
        aria-invalid={failure !== undefined || undefined}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return
          event.preventDefault()
          void send()
        }}
      />
      <div className="flex justify-end">
        {running ? (
          <Button size="icon" aria-label="Stop" onClick={() => void stop()}>
            <SquareIcon />
          </Button>
        ) : (
          <Button
            size="icon"
            aria-label="Send"
            disabled={text === "" || sending}
            onClick={() => void send()}
          >
            <ArrowUpIcon />
          </Button>
        )}
      </div>
      {failure !== undefined && <FieldError>{failure}</FieldError>}
    </Field>
  )
}

function Transcript({ turns, name }: { turns: TurnBlock[]; name: string }) {
  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="mx-auto my-6 w-full max-w-3xl">
            {turns.map((turn) => (
              <MessageScrollerItem key={turn.turnId} messageId={turn.turnId} scrollAnchor>
                <Turn turn={turn} name={name} />
              </MessageScrollerItem>
            ))}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  )
}

function Turn({ turn, name }: { turn: TurnBlock; name: string }) {
  const { startedBy } = turn
  return (
    <div className="flex flex-col gap-3 px-6">
      <Marker variant="border">
        <MarkerIcon>{startedBy.kind === "routine" ? <RepeatIcon /> : <UserIcon />}</MarkerIcon>
        <MarkerContent>
          {startedBy.kind === "routine" ? `Routine ${startedBy.name}` : "You"} ·{" "}
          <time dateTime={turn.startedAt}>{startedAt(turn.startedAt)}</time>
        </MarkerContent>
      </Marker>
      <Message align="end">
        <MessageContent>
          <Bubble variant="secondary" align="end">
            <BubbleContent className="whitespace-pre-wrap">{turn.request}</BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
      <Message>
        <MessageContent>
          <MessageHeader>{name}</MessageHeader>
          {turn.steps.map((step, index) =>
            step.kind === "text" ? (
              <Bubble key={index} variant="ghost">
                <BubbleContent className="whitespace-pre-wrap">{step.text}</BubbleContent>
              </Bubble>
            ) : (
              <ToolMarker
                key={step.callId}
                step={step}
                waiting={turn.approval?.callId === step.callId}
              />
            ),
          )}
          <EndMarker turn={turn} />
        </MessageContent>
      </Message>
    </div>
  )
}

function ToolMarker({ step, waiting }: { step: ToolStep; waiting: boolean }) {
  const { icon, decision } = gateDecision(step, waiting)
  const call = [step.name, mainArgument(step.arguments)].filter(Boolean).join(" ")
  return (
    <Marker>
      <MarkerIcon>{icon}</MarkerIcon>
      <MarkerContent>{`${call} · ${decision}`}</MarkerContent>
    </Marker>
  )
}

/** How the gate decided a call, or, once it let the call run, how long the call took. */
function gateDecision(
  { gate, durationMs }: ToolStep,
  waiting: boolean,
): { icon: ReactNode; decision: string } {
  if (gate === undefined) {
    return waiting
      ? { icon: <ShieldAlertIcon />, decision: "waiting for you" }
      : { icon: <WrenchIcon />, decision: "running" }
  }
  if (gate.action === "deny") {
    const decision = gate.decidedBy === "user" ? "denied by you" : `denied by policy: ${gate.rule}`
    return { icon: <BanIcon />, decision }
  }
  if (gate.decidedBy === "user") return { icon: <CheckIcon />, decision: "approved by you" }
  return {
    icon: <WrenchIcon />,
    decision: durationMs === undefined ? "running" : `${durationMs} ms`,
  }
}

/** The first text argument. Tools take the thing they act on first: the path, the command, the pattern. */
function mainArgument(args: Record<string, JsonValue>): string | undefined {
  return Object.values(args).find((value): value is string => typeof value === "string")
}

function EndMarker({ turn }: { turn: TurnBlock }) {
  const { icon, outcome } = turnOutcome(turn)
  return (
    <Marker>
      <MarkerIcon>{icon}</MarkerIcon>
      <MarkerContent>{outcome}</MarkerContent>
    </Marker>
  )
}

/** How the turn ended, or that it is still working. */
function turnOutcome({ end, steps }: TurnBlock): { icon: ReactNode; outcome: ReactNode } {
  switch (end?.kind) {
    case undefined:
      return { icon: <Spinner />, outcome: <span className="shimmer">Working</span> }
    case "done": {
      const calls = steps.filter((step) => step.kind === "tool").length
      const tokens = end.tokens.toLocaleString()
      return {
        icon: <CheckIcon />,
        outcome: `Done · ${calls} ${calls === 1 ? "step" : "steps"} · ${tokens} tokens`,
      }
    }
    case "failed":
      return { icon: <CircleAlertIcon />, outcome: `Failed: ${end.message} (${end.code})` }
    case "stopped":
      return { icon: <CircleStopIcon />, outcome: "Stopped" }
  }
}

function startedAt(timestamp: string): string {
  return new Date(timestamp).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

function TranscriptSkeleton() {
  return (
    <output aria-label="Loading the thread" className="flex flex-col gap-6 p-6">
      <Skeleton className="h-10 w-2/5 self-end" />
      <Skeleton className="h-24 w-3/5" />
      <Skeleton className="h-10 w-1/3 self-end" />
      <Skeleton className="h-16 w-1/2" />
    </output>
  )
}
