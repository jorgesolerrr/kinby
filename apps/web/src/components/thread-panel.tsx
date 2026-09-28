import type { InstanceClient, JsonValue } from "@kinby/contract"
import {
  type ReactNode,
  type Ref,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"

import { ThreadHeader } from "@/components/thread-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
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
import type { ParkedApproval, ToolStep, TurnBlock, TurnEnd } from "@/lib/timeline"
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
  XIcon,
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
  const approval = latest?.approval
  const reasonField = useRef<HTMLInputElement>(null)
  // A thread that opens on a parked approval has no composer to take focus. An approval that parks
  // later leaves focus where it is, so keys typed for the composer cannot deny it.
  useEffect(() => {
    if (replayed) takeFocus(reasonField.current)
  }, [replayed])

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
            {/* Nothing else is sent while the turn waits on the user. */}
            {approval === undefined ? (
              <Composer
                client={client}
                threadId={threadId}
                name={name}
                running={latest !== undefined && latest.end === undefined}
              />
            ) : (
              <ApprovalPanel
                key={approval.approvalId}
                reasonField={reasonField}
                client={client}
                threadId={threadId}
                approval={approval}
              />
            )}
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
  const field = useRef<HTMLTextAreaElement>(null)
  const text = message.trim()
  // The composer shows when a thread opens and when an answered approval gives way to it.
  useEffect(() => takeFocus(field.current), [])

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
        ref={field}
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

/**
 * The approval a turn is parked on, in the composer's place: approve, deny with an optional reason
 * the model reads, or stop the turn. Enter in the reason denies with it.
 */
function ApprovalPanel({
  reasonField,
  client,
  threadId,
  approval,
}: {
  reasonField: Ref<HTMLInputElement>
  client: Pick<InstanceClient, "call">
  threadId: string
  approval: ParkedApproval
}) {
  const reasonId = useId()
  const [typed, setTyped] = useState("")
  // An answer is final. The panel goes when the turn's next event arrives.
  const [answering, setAnswering] = useState(false)
  const [failure, setFailure] = useState<string>()
  const call = [approval.name, mainArgument(approval.arguments)].filter(Boolean).join(" ")

  const answer = async (work: () => Promise<unknown>) => {
    setAnswering(true)
    try {
      await work()
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
      setAnswering(false)
    }
  }
  const respond = (decision: "approve" | "deny") => {
    const denial = typed.trim()
    return answer(() =>
      client.call("thread.approval.respond", {
        thread_id: threadId,
        approval_id: approval.approvalId,
        decision,
        ...(decision === "deny" && denial !== "" && { reason: denial }),
      }),
    )
  }
  const stop = () => answer(() => client.call("thread.turn.interrupt", { thread_id: threadId }))

  return (
    <section aria-label={`Approve ${call}?`}>
      <Card size="sm">
        <CardHeader>
          <CardTitle>{call}</CardTitle>
          <CardDescription>Waiting for your answer</CardDescription>
          <CardAction>
            <Badge variant="outline">{approval.rule}</Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-3">
            <pre className="max-h-40 overflow-auto font-mono text-xs whitespace-pre-wrap">
              {JSON.stringify(approval.arguments, null, 2)}
            </pre>
            <Field data-invalid={failure !== undefined || undefined}>
              <FieldLabel htmlFor={reasonId}>Reason</FieldLabel>
              <Input
                ref={reasonField}
                id={reasonId}
                placeholder="Optional: why not, or what to do instead"
                value={typed}
                disabled={answering}
                aria-invalid={failure !== undefined || undefined}
                onChange={(event) => setTyped(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || event.nativeEvent.isComposing) return
                  event.preventDefault()
                  void respond("deny")
                }}
              />
              {failure !== undefined && <FieldError>{failure}</FieldError>}
            </Field>
          </div>
        </CardContent>
        <CardFooter>
          <div className="flex w-full items-center gap-2">
            <Button disabled={answering} onClick={() => void respond("approve")}>
              <CheckIcon data-icon="inline-start" />
              Approve
            </Button>
            <Button variant="outline" disabled={answering} onClick={() => void respond("deny")}>
              <XIcon data-icon="inline-start" />
              Deny
            </Button>
            <Button
              variant="ghost"
              className="ml-auto"
              disabled={answering}
              onClick={() => void stop()}
            >
              <CircleStopIcon data-icon="inline-start" />
              Stop the turn
            </Button>
          </div>
        </CardFooter>
      </Card>
    </section>
  )
}

/** Focus `field`, unless the user is typing in another one, like the title in the header. */
function takeFocus(field: HTMLElement | null) {
  const active = document.activeElement
  if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) return
  field?.focus()
}

function Transcript({ turns, name }: { turns: TurnBlock[]; name: string }) {
  return (
    // A turn opens from its first marker, without a peek at the turn before it.
    <MessageScrollerProvider
      autoScroll
      defaultScrollPosition="last-anchor"
      scrollPreviousItemPeek={0}
    >
      {/* The scroller leaves its content's margin out when it decides where a thread opens. */}
      <MessageScroller className="my-6 flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="mx-auto w-full max-w-3xl">
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
                end={turn.end}
              />
            ),
          )}
          <EndMarker turn={turn} />
        </MessageContent>
      </Message>
    </div>
  )
}

function ToolMarker({
  step,
  waiting,
  end,
}: {
  step: ToolStep
  waiting: boolean
  end: TurnEnd | undefined
}) {
  const { icon, decision } = gateDecision(step, waiting, end)
  const call = [step.name, mainArgument(step.arguments)].filter(Boolean).join(" ")
  return (
    <Marker>
      <MarkerIcon>{icon}</MarkerIcon>
      <MarkerContent>{`${call} · ${decision}`}</MarkerContent>
    </Marker>
  )
}

/**
 * How the gate decided a call and, once it let the call run, how long the call took. A call the
 * gate never decided did not run if its turn has ended.
 */
function gateDecision(
  { gate, durationMs }: ToolStep,
  waiting: boolean,
  end: TurnEnd | undefined,
): { icon: ReactNode; decision: string } {
  if (gate === undefined && durationMs === undefined && end !== undefined) {
    return { icon: <BanIcon />, decision: `not run, turn ${end.kind}` }
  }
  if (gate === undefined) {
    return waiting
      ? { icon: <ShieldAlertIcon />, decision: "waiting for you" }
      : { icon: <WrenchIcon />, decision: "running" }
  }
  if (gate.action === "deny") {
    const decision =
      gate.decidedBy === "policy"
        ? `denied by policy: ${gate.rule}`
        : gate.reason
          ? `denied by you: ${gate.reason}`
          : "denied by you"
    return { icon: <BanIcon />, decision }
  }
  const took = durationMs === undefined ? "running" : `${durationMs} ms`
  if (gate.decidedBy === "user") {
    return { icon: <CheckIcon />, decision: `approved by you · ${took}` }
  }
  return { icon: <WrenchIcon />, decision: took }
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
