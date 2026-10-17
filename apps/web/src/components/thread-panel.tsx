import type { Avatar as AvatarChoice, InstanceClient, JsonValue } from "@kinby/contract"
import {
  type ComponentProps,
  type ReactNode,
  type Ref,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import { code } from "@streamdown/code"
import { defaultRehypePlugins, type ExtraProps, Streamdown, type StreamdownProps } from "streamdown"

import { InstanceAvatar } from "@/components/instance-avatar"
import { ThreadHeader } from "@/components/thread-header"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
  useMessageScroller,
} from "@/components/ui/message-scroller"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { reason } from "@/lib/operation"
import { originLabel } from "@/lib/origin"
import { navigate, nodePath, openNode, useSelectedTurnId } from "@/lib/selection"
import { threadStore } from "@/lib/thread-store"
import type { ParkedApproval, Recap, ToolStep, TurnBlock, TurnEnd } from "@/lib/timeline"
import {
  BanIcon,
  BrainIcon,
  CheckIcon,
  CircleAlertIcon,
  CircleStopIcon,
  CornerDownLeftIcon,
  FactoryIcon,
  HouseIcon,
  MinusIcon,
  RepeatIcon,
  ShieldAlertIcon,
  SquareIcon,
  TriangleAlertIcon,
  UserIcon,
  WrenchIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react"

/** A thread's turns as a timeline, followed live once its history has loaded. */
export function ThreadPanel({
  client,
  instanceId,
  threadId,
  name,
  avatar,
}: {
  client: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange">
  /** The hub's ID for the instance, which its pages' links name. */
  instanceId: string
  threadId: string
  /** What the instance is called. */
  name: string
  /** The instance's avatar, beside what it says. */
  avatar: AvatarChoice
}) {
  const speaker = { name, avatar }
  const store = threadStore(client, threadId)
  useEffect(() => store.follow(), [store])
  const { timeline, replayed, failure } = useSyncExternalStore(store.onChange, store.view)
  const latest = timeline.turns.at(-1)
  const approval = latest?.approval
  const reasonField = useRef<HTMLInputElement>(null)
  const approvalPanel = useRef<HTMLElement>(null)
  // A thread that opens on a parked approval has no composer to take focus, so its reason does.
  useEffect(() => {
    if (replayed) takeFocus(reasonField.current)
  }, [replayed])
  // An approval that parks later takes the composer's place, and focus falls to the page. The panel
  // itself catches it, not a field in it, so keys typed for the composer cannot deny it.
  useEffect(() => {
    if (document.activeElement === document.body) approvalPanel.current?.focus()
  }, [approval?.approvalId])

  if (failure !== undefined) {
    return (
      <div className="mx-auto w-full max-w-3xl px-6 py-6">
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>The thread did not load</AlertTitle>
          <AlertDescription>
            <p>{failure}</p>
            <p>Reload to try again.</p>
          </AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
              Reload
            </Button>
          </AlertAction>
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
          <Transcript turns={timeline.turns} instanceId={instanceId} speaker={speaker} />
          <div className="mx-auto w-full max-w-3xl px-6 pb-6">
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
                ref={approvalPanel}
                reasonField={reasonField}
                client={client}
                threadId={threadId}
                approval={approval}
                speaker={speaker}
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

/** The instance as the transcript and the approval panel show it. */
interface Speaker {
  name: string
  avatar: AvatarChoice
}

/**
 * Enter sends the message as a new turn, and Shift+Enter starts a new line. While a turn runs,
 * Send becomes Stop, and Escape stops it too.
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
      {/* Send sits inside the field, and a long draft scrolls in it instead of pushing the
          transcript away. */}
      <InputGroup>
        <InputGroupAddon align="inline-start">
          <span aria-hidden className="font-mono text-kin">
            ›
          </span>
        </InputGroupAddon>
        <InputGroupTextarea
          ref={field}
          variant="prompt"
          className="max-h-48"
          aria-label={`Message ${name}`}
          placeholder={running ? `${name} is working…` : `Message ${name}`}
          value={message}
          aria-invalid={failure !== undefined || undefined}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && running && !event.nativeEvent.isComposing) {
              event.preventDefault()
              void stop()
              return
            }
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return
            event.preventDefault()
            void send()
          }}
        />
        <InputGroupAddon align="inline-end">
          {running ? (
            <InputGroupButton size="sm" variant="outline" onClick={() => void stop()}>
              <SquareIcon data-icon="inline-start" className="fill-current" />
              Stop
              <kbd aria-hidden className="font-mono text-xs text-muted-foreground">
                Esc
              </kbd>
            </InputGroupButton>
          ) : (
            <InputGroupButton
              size="sm"
              variant="default"
              // aria-disabled, so an empty draft leaves the field looking ready to type in.
              aria-disabled={text === "" || sending}
              onClick={() => void send()}
            >
              Send
              <CornerDownLeftIcon data-icon="inline-end" />
            </InputGroupButton>
          )}
        </InputGroupAddon>
      </InputGroup>
      {failure !== undefined && <FieldError>{failure}</FieldError>}
    </Field>
  )
}

/** The button pressed in an approval panel. */
type Answer = "approve" | "deny" | "stop"

/**
 * The approval a turn is parked on, in the composer's place: approve, deny with an optional reason
 * the model reads, or stop the turn. Enter in the reason denies with it.
 */
function ApprovalPanel({
  ref,
  reasonField,
  client,
  threadId,
  approval,
  speaker,
}: {
  ref: Ref<HTMLElement>
  reasonField: Ref<HTMLInputElement>
  client: Pick<InstanceClient, "call">
  threadId: string
  approval: ParkedApproval
  speaker: Speaker
}) {
  const reasonId = useId()
  const [typed, setTyped] = useState("")
  // An answer is final. The panel goes when the turn's next event arrives.
  const [answering, setAnswering] = useState<Answer>()
  const [failure, setFailure] = useState<string>()
  const call = [approval.name, mainArgument(approval.arguments)].filter(Boolean).join(" ")

  const answer = async (pressed: Answer, work: () => Promise<unknown>) => {
    setAnswering(pressed)
    try {
      await work()
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
      setAnswering(undefined)
    }
  }
  const respond = (decision: "approve" | "deny") => {
    const denial = typed.trim()
    return answer(decision, () =>
      client.call("thread.approval.respond", {
        thread_id: threadId,
        approval_id: approval.approvalId,
        decision,
        ...(decision === "deny" && denial !== "" && { reason: denial }),
      }),
    )
  }
  const stop = () =>
    answer("stop", () => client.call("thread.turn.interrupt", { thread_id: threadId }))
  const busy = answering !== undefined

  return (
    <section
      ref={ref}
      tabIndex={-1}
      aria-label={`Approve ${call}?`}
      className="flex gap-3 rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      {/* The instance asks in its own voice, beside the avatar it speaks with in the transcript. */}
      <InstanceAvatar avatar={speaker.avatar} name={speaker.name} />
      <Card className="min-w-0 flex-1">
        <CardContent>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <h2 className="text-lg font-semibold">Can I run {approval.name}?</h2>
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <ShieldAlertIcon aria-hidden className="size-3.5" />
                {approval.rule}
              </p>
            </div>
            <pre className="max-h-40 overflow-auto rounded-lg bg-muted p-4 font-mono text-xs whitespace-pre-wrap">
              {JSON.stringify(approval.arguments, null, 2)}
            </pre>
            <Field data-invalid={failure !== undefined || undefined}>
              <FieldLabel htmlFor={reasonId}>Reason to deny (optional)</FieldLabel>
              <Input
                ref={reasonField}
                id={reasonId}
                placeholder="Use the staging database instead"
                value={typed}
                disabled={busy}
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
            {/* Below md the buttons grow to a 44 px tap target, and Stop the turn wraps before it overflows. */}
            <div className="flex w-full flex-wrap items-center gap-2">
              <Button
                size="lg"
                className="max-md:h-11"
                disabled={busy}
                onClick={() => void respond("approve")}
              >
                {answering === "approve" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <CheckIcon data-icon="inline-start" />
                )}
                Approve
              </Button>
              <Button
                size="lg"
                variant="outline"
                className="max-md:h-11"
                disabled={busy}
                onClick={() => void respond("deny")}
              >
                {answering === "deny" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <XIcon data-icon="inline-start" />
                )}
                Deny
              </Button>
              <Button
                variant="link"
                className="ml-auto max-md:h-11"
                disabled={busy}
                onClick={() => void stop()}
              >
                {answering === "stop" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <CircleStopIcon data-icon="inline-start" />
                )}
                Stop the turn
              </Button>
            </div>
          </div>
        </CardContent>
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

function Transcript({
  turns,
  instanceId,
  speaker,
}: {
  turns: TurnBlock[]
  instanceId: string
  speaker: Speaker
}) {
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
                <Turn turn={turn} instanceId={instanceId} speaker={speaker} />
              </MessageScrollerItem>
            ))}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
      <LinkedTurn turns={turns} />
    </MessageScrollerProvider>
  )
}

/**
 * Scrolls to the turn the URL names once it is in the transcript. Each turn named is scrolled to
 * once, so the events that follow leave the reader where they are. A URL that names no turn
 * forgets the last one, so going Back to it scrolls there again.
 */
function LinkedTurn({ turns }: { turns: TurnBlock[] }) {
  const turnId = useSelectedTurnId()
  const present = turns.some((turn) => turn.turnId === turnId)
  const { scrollToMessage } = useMessageScroller()
  const reached = useRef<string>(undefined)
  useEffect(() => {
    if (turnId === undefined) reached.current = undefined
    if (turnId === undefined || !present || reached.current === turnId) return
    reached.current = turnId
    scrollToMessage(turnId)
  }, [turnId, present, scrollToMessage])
  return null
}

/** What started a turn the user did not: the instance acting on its own, so it wears ember. */
const STARTED_BY_ICONS: Record<Exclude<TurnBlock["startedBy"]["kind"], "user">, LucideIcon> = {
  routine: RepeatIcon,
  "factory-run": FactoryIcon,
  "home-instance": HouseIcon,
}

/**
 * The user's request in a bubble on the right, or what started the turn on its own. Then the
 * instance beside its avatar: each reply in a tinted bubble, and the tool calls between replies as
 * one rail of steps.
 */
function Turn({
  turn,
  instanceId,
  speaker,
}: {
  turn: TurnBlock
  instanceId: string
  speaker: Speaker
}) {
  const { startedBy } = turn
  const started = (
    <>
      {originLabel(startedBy)} · <time dateTime={turn.startedAt}>{startedAt(turn.startedAt)}</time>
    </>
  )
  return (
    <div className="flex flex-col gap-4 px-6">
      {startedBy.kind === "user" ? (
        <div className="flex items-end justify-end gap-3">
          <div className="flex min-w-0 flex-col items-end gap-1">
            <p className="font-mono text-xs text-muted-foreground">{started}</p>
            <Bubble align="end">
              <BubbleContent className="whitespace-pre-wrap">{turn.request}</BubbleContent>
            </Bubble>
          </div>
          <Avatar size="sm" aria-hidden>
            <AvatarFallback>
              <UserIcon className="size-3.5" />
            </AvatarFallback>
          </Avatar>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
            <StartedByNode kind={startedBy.kind} />
            <span>{started}</span>
          </p>
          <p className="pl-7 text-sm whitespace-pre-wrap text-muted-foreground">{turn.request}</p>
        </div>
      )}
      <div className="flex gap-3">
        <InstanceAvatar avatar={speaker.avatar} name={speaker.name} />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <p className="font-mono text-xs text-muted-foreground">{speaker.name}</p>
          {railed(turn.steps).map((run) =>
            run.kind === "text" ? (
              <Bubble key={run.key} variant="tinted">
                <BubbleContent>
                  <Reply text={run.text} />
                </BubbleContent>
              </Bubble>
            ) : (
              <ol key={run.key} className="flex flex-col">
                {run.steps.map((step, index) => (
                  <ToolStepRow
                    key={step.callId}
                    step={step}
                    waiting={turn.approval?.callId === step.callId}
                    end={turn.end}
                    last={index === run.steps.length - 1}
                  />
                ))}
              </ol>
            ),
          )}
          <EndMarker turn={turn} />
          {turn.recap !== undefined && <RecapMarker recap={turn.recap} instanceId={instanceId} />}
        </div>
      </div>
    </div>
  )
}

function StartedByNode({ kind }: { kind: keyof typeof STARTED_BY_ICONS }) {
  const Icon = STARTED_BY_ICONS[kind]
  return (
    <span
      aria-hidden
      className="grid size-5 shrink-0 place-items-center rounded-full bg-ember text-on-ember"
    >
      <Icon className="size-3" />
    </span>
  )
}

type Run =
  | { kind: "text"; key: string; text: string }
  | { kind: "rail"; key: string; steps: ToolStep[] }

/** A turn's steps with each stretch of consecutive tool calls gathered into one rail. */
function railed(steps: TurnBlock["steps"]): Run[] {
  const runs: Run[] = []
  steps.forEach((step, index) => {
    const last = runs.at(-1)
    if (step.kind === "text") runs.push({ kind: "text", key: `text-${index}`, text: step.text })
    else if (last?.kind === "rail") last.steps.push(step)
    else runs.push({ kind: "rail", key: step.callId, steps: [step] })
  })
  return runs
}

/** How Streamdown renders a reply, built once so its memoization holds while a reply streams. */
const replyOptions = {
  // Streamdown draws bold as a styled span. A reply's bold stays a `strong`.
  components: { strong: "strong", a: ReplyLink },
  // Streamdown renders raw HTML through its `raw` plugin. Without it, HTML in a reply stays text.
  rehypePlugins: Object.entries(defaultRehypePlugins)
    .filter(([name]) => name !== "raw")
    .map(([, plugin]) => plugin),
  // An image would load its URL the moment the thread opens, so a reply can't carry one.
  disallowedElements: ["img"],
  plugins: { code },
  // Links open straight, without Streamdown's confirmation dialog.
  linkSafety: { enabled: false },
  // A reply's headings stay below the thread's own title, in kinby's heading face.
  className:
    "[&_h1]:text-lg [&_h2]:text-base [&_h3]:text-sm [&_h4]:text-sm [&_:is(h1,h2,h3,h4)]:mt-5 [&_:is(h1,h2,h3,h4)]:font-heading [&_:is(h1,h2,h3,h4)]:font-semibold",
} satisfies StreamdownProps

/** What the instance wrote, as the Markdown it writes in. */
function Reply({ text }: { text: string }) {
  return <Streamdown {...replyOptions}>{text}</Streamdown>
}

/**
 * A link in a reply. A page of the app opens in place, like the sidebar's links, and any other link
 * in a new tab.
 */
function ReplyLink({ href, title, children }: ComponentProps<"a"> & ExtraProps) {
  const className = "wrap-anywhere font-medium text-primary underline"
  // Streamdown's link hardening has already made a path like `//host` one on this origin.
  if (!href?.startsWith("/")) {
    return (
      <a className={className} href={href} title={title} rel="noreferrer" target="_blank">
        {children}
      </a>
    )
  }
  return (
    <a
      className={className}
      href={href}
      title={title}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
          return
        event.preventDefault()
        navigate(href)
      }}
    >
      {children}
    </a>
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
  return (
    <li className="flex gap-3">
      <span className="relative flex w-5 shrink-0 justify-center">
        {!last && <span aria-hidden className="absolute top-6 bottom-0 w-px bg-border" />}
        <span
          aria-hidden
          className={
            waiting
              ? "mt-0.5 grid size-5 place-items-center rounded-full bg-primary text-primary-foreground"
              : "mt-0.5 grid size-5 place-items-center rounded-full bg-muted text-muted-foreground"
          }
        >
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
 * How the gate decided a call and, once it let the call run, how long the call took. A call the
 * gate never decided did not run if its turn has ended.
 */
function gateDecision(
  { gate, durationMs }: ToolStep,
  waiting: boolean,
  end: TurnEnd | undefined,
): { icon: LucideIcon; decision: string } {
  if (gate === undefined && durationMs === undefined && end !== undefined) {
    return { icon: BanIcon, decision: `not run, turn ${end.kind}` }
  }
  if (gate === undefined) {
    return waiting
      ? { icon: ShieldAlertIcon, decision: "waiting for you" }
      : { icon: WrenchIcon, decision: "running" }
  }
  if (gate.action === "deny") {
    const decision =
      gate.decidedBy === "policy"
        ? `denied by policy: ${gate.rule}`
        : gate.reason
          ? `denied by you: ${gate.reason}`
          : "denied by you"
    return { icon: BanIcon, decision }
  }
  const took = durationMs === undefined ? "running" : `${durationMs.toLocaleString("en")} ms`
  if (gate.decidedBy === "user") {
    return { icon: CheckIcon, decision: `approved by you · ${took}` }
  }
  return { icon: WrenchIcon, decision: took }
}

/** The first text argument. Tools take the thing they act on first: the path, the command, the pattern. */
function mainArgument(args: Record<string, JsonValue>): string | undefined {
  return Object.values(args).find((value): value is string => typeof value === "string")
}

function EndMarker({ turn }: { turn: TurnBlock }) {
  const { icon, outcome, variant } = turnOutcome(turn)
  return (
    <Marker variant={variant}>
      <MarkerIcon>{icon}</MarkerIcon>
      <MarkerContent>
        <span className="font-mono text-xs">{outcome}</span>
      </MarkerContent>
    </Marker>
  )
}

/** How the turn ended, or that it is still working. */
function turnOutcome({ end, steps }: TurnBlock): {
  icon: ReactNode
  outcome: ReactNode
  variant?: "destructive"
} {
  switch (end?.kind) {
    case undefined:
      return { icon: <Spinner />, outcome: <span className="shimmer">Working</span> }
    case "done": {
      const calls = steps.filter((step) => step.kind === "tool").length
      const tokens = end.tokens.toLocaleString("en")
      return {
        icon: <CheckIcon />,
        outcome: `Done · ${calls} ${calls === 1 ? "step" : "steps"} · ${tokens} tokens`,
      }
    }
    case "failed":
      return {
        icon: <CircleAlertIcon />,
        outcome: `Failed: ${end.message} (${end.code}). Send a message to try again.`,
        variant: "destructive",
      }
    case "stopped":
      return { icon: <CircleStopIcon />, outcome: "Stopped" }
  }
}

/** What the turn's recap left in memory, linking to the episode it kept. */
function RecapMarker({ recap, instanceId }: { recap: Recap; instanceId: string }) {
  switch (recap.kind) {
    case "episode":
      return (
        <Marker>
          <MarkerIcon>
            <BrainIcon />
          </MarkerIcon>
          <MarkerContent>
            <a
              className="font-mono text-xs"
              href={nodePath(instanceId, recap.node)}
              onClick={(event) => {
                event.preventDefault()
                openNode(instanceId, recap.node)
              }}
            >
              Recapped
            </a>
          </MarkerContent>
        </Marker>
      )
    case "none":
      return (
        <Marker>
          <MarkerIcon>
            <MinusIcon />
          </MarkerIcon>
          <MarkerContent>
            <span className="font-mono text-xs">No episode</span>
          </MarkerContent>
        </Marker>
      )
    case "failed":
      return (
        // The reason shows inline, so touch and keyboard readers get it too.
        <Marker className="items-start">
          <MarkerIcon>
            <TriangleAlertIcon />
          </MarkerIcon>
          <MarkerContent className="flex flex-col">
            <span className="font-mono text-xs">Recap failed</span>
            <span className="font-mono text-xs">{recap.message}</span>
          </MarkerContent>
        </Marker>
      )
  }
}

function startedAt(timestamp: string): string {
  return new Date(timestamp).toLocaleString("en", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })
}

function TranscriptSkeleton() {
  return (
    // Laid out like the transcript: a request bubble, then the reply as lines of text.
    <output
      aria-label="Loading the thread"
      className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-6"
    >
      <Skeleton className="h-10 w-2/5 self-end" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
        <Skeleton className="h-4 w-2/3" />
      </div>
      <Skeleton className="h-10 w-1/3 self-end" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </output>
  )
}
