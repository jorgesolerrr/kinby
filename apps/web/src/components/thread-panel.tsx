import type {
  ApprovalDecision,
  Avatar as AvatarChoice,
  Client,
  Clock,
  InstanceClient,
  JsonValue,
  Notification,
} from "@kinby/contract"
import {
  type ComponentProps,
  type ReactNode,
  type Ref,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import { code } from "@streamdown/code"
import { defaultRehypePlugins, type ExtraProps, Streamdown, type StreamdownProps } from "streamdown"

import { InstanceAvatar } from "@/components/instance-avatar"
import { LiveBrowser, type LiveBrowserInput } from "@/components/live-browser"
import { ThreadHeader } from "@/components/thread-header"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
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
import { useRead } from "@/hooks/use-read"
import { reason } from "@/lib/operation"
import { originLabel } from "@/lib/origin"
import {
  navigate,
  nodePath,
  openNode,
  openView,
  useSelectedTurnId,
  viewPath,
} from "@/lib/selection"
import { threadStore } from "@/lib/thread-store"
import type {
  NotificationStep,
  ParkedApproval,
  ParkedTakeover,
  Recap,
  ToolStep,
  TurnBlock,
  TurnEnd,
  ViewStep,
} from "@/lib/timeline"
import {
  BanIcon,
  BellIcon,
  BookCheckIcon,
  BrainIcon,
  CheckCheckIcon,
  CheckIcon,
  CircleAlertIcon,
  CircleStopIcon,
  CornerDownLeftIcon,
  FactoryIcon,
  FlagIcon,
  HouseIcon,
  LayoutDashboardIcon,
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

/**
 * How often the panel reads the thread's notifications again while one is still sending or the
 * hub has yet to apply an answer to one.
 */
const SETTLING_READ_MS = 5_000

/**
 * A thread's turns as a timeline, followed live once its history has loaded. The hub tells how
 * each notification the thread raised was delivered, and takes the user's answer to its options.
 */
export function ThreadPanel({
  client,
  hub,
  clock,
  instanceId,
  threadId,
  name,
  avatar,
}: {
  client: Pick<InstanceClient, "call" | "subscribe" | "state" | "onStateChange">
  hub: Pick<Client, "call" | "subscribe">
  clock: Clock
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
  const notifications = useNotifications(hub, clock, threadId, timeline.turns)
  const latest = timeline.turns.at(-1)
  const approval = latest?.approval
  const takeover = latest?.takeover
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
          <Transcript
            turns={timeline.turns}
            notifications={notifications}
            instanceId={instanceId}
            speaker={speaker}
          />
          <div className="mx-auto w-full max-w-3xl px-6 pb-6">
            {/* Nothing else is sent while the turn waits on the user. */}
            {approval !== undefined ? (
              <ApprovalPanel
                key={approval.approvalId}
                ref={approvalPanel}
                reasonField={reasonField}
                client={client}
                threadId={threadId}
                approval={approval}
                routine={latest?.startedBy.kind === "routine" ? latest.startedBy.name : undefined}
                speaker={speaker}
              />
            ) : latest !== undefined && takeover !== undefined ? (
              <TakeoverPanel
                key={takeover.takeoverId}
                client={client}
                hub={hub}
                instanceId={instanceId}
                threadId={threadId}
                turnId={latest.turnId}
                takeover={takeover}
                speaker={speaker}
              />
            ) : (
              <Composer
                client={client}
                threadId={threadId}
                name={name}
                running={latest !== undefined && latest.end === undefined}
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

/** What the hub keeps of the thread's notifications, and the answer to one of their options. */
interface Notifications {
  kept: ReadonlyMap<string, Notification>
  answer: (notificationId: string, option: string) => Promise<void>
}

/**
 * The hub's notifications of the thread, read again whenever the hub takes another or a turn
 * starts, and while one is still settling.
 */
function useNotifications(
  hub: Pick<Client, "call">,
  clock: Clock,
  threadId: string,
  turns: TurnBlock[],
): Notifications {
  const accepted = turns
    .flatMap((turn) => turn.steps)
    .filter((step) => step.kind === "notification" && step.hub?.kind === "accepted").length
  const list = useCallback(
    async () =>
      accepted === 0
        ? []
        : (await hub.call("notification.list", { thread_id: threadId })).notifications,
    [hub, threadId, accepted],
  )
  const { value, readAgain } = useRead(list, clock)
  // A reply through a channel shows in the thread only as the turn it starts.
  const started = turns.length
  const seen = useRef(started)
  useEffect(() => {
    if (seen.current === started) return
    seen.current = started
    readAgain()
  }, [started, readAgain])
  const settling =
    value?.some(
      (kept) =>
        kept.deliveries.some((delivery) => delivery.status === "pending") ||
        kept.replies.some((reply) => reply.status === "received"),
    ) ?? false
  useEffect(
    () => (settling ? clock.after(SETTLING_READ_MS, readAgain) : undefined),
    [settling, value, clock, readAgain],
  )
  const answer = async (notificationId: string, option: string) => {
    await hub.call("notification.reply", { notification_id: notificationId, option })
    readAgain()
  }
  return {
    kept: new Map((value ?? []).map((kept) => [kept.notification_id, kept])),
    answer,
  }
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

/** The gate rule of a call to an authored tool, which the user can mark read-only. */
const AUTHORED_RULE = "authored.write"

/** The button pressed in an approval panel. */
type Answer = ApprovalDecision | "stop"

/**
 * The approval a turn is parked on, in the composer's place: approve, deny with an optional reason
 * the model reads, or stop the turn. Enter in the reason denies with it. On a routine's turn it
 * also offers to approve the tool in every run of that routine, and on an authored tool's call to
 * mark the tool read-only.
 */
function ApprovalPanel({
  ref,
  reasonField,
  client,
  threadId,
  approval,
  routine,
  speaker,
}: {
  ref: Ref<HTMLElement>
  reasonField: Ref<HTMLInputElement>
  client: Pick<InstanceClient, "call">
  threadId: string
  approval: ParkedApproval
  /** The routine whose turn parked on the approval. */
  routine: string | undefined
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
  const respond = (decision: ApprovalDecision) => {
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
            {approval.preview !== undefined && (
              <pre className="max-h-80 overflow-auto rounded-lg bg-muted p-4 font-mono text-xs whitespace-pre-wrap">
                {approval.preview}
              </pre>
            )}
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
              {routine !== undefined && (
                <Button
                  size="lg"
                  variant="secondary"
                  className="max-md:h-11"
                  disabled={busy}
                  onClick={() => void respond("allow_in_routine")}
                >
                  {answering === "allow_in_routine" ? (
                    <Spinner data-icon="inline-start" />
                  ) : (
                    <CheckCheckIcon data-icon="inline-start" />
                  )}
                  Always allow in this routine
                </Button>
              )}
              {approval.rule === AUTHORED_RULE && (
                <Button
                  size="lg"
                  variant="secondary"
                  className="max-md:h-11"
                  disabled={busy}
                  onClick={() => void respond("mark_read_only")}
                >
                  {answering === "mark_read_only" ? (
                    <Spinner data-icon="inline-start" />
                  ) : (
                    <BookCheckIcon data-icon="inline-start" />
                  )}
                  Mark read-only
                </Button>
              )}
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

/**
 * The takeover a turn is parked on, in the composer's place: why the agent asks, and its browser's
 * page live, which takes the user's mouse and keyboard. The hub keeps the page, so the user drives
 * it there. Finish resumes the turn with the page as the user left it, saved first as a profile
 * when the user names one.
 */
function TakeoverPanel({
  client,
  hub,
  instanceId,
  threadId,
  turnId,
  takeover,
  speaker,
}: {
  client: Pick<InstanceClient, "call">
  hub: Pick<Client, "call" | "subscribe">
  instanceId: string
  threadId: string
  turnId: string
  takeover: ParkedTakeover
  speaker: Speaker
}) {
  const page = { instance_id: instanceId, turn_id: turnId }
  const ids = { ...page, thread_id: threadId, takeover_id: takeover.takeoverId }
  const watch = useCallback(
    () => hub.subscribe("thread.takeover.start", { instance_id: instanceId, turn_id: turnId }),
    [hub, instanceId, turnId],
  )
  const profileId = useId()
  const [profile, setProfile] = useState("")
  const [failure, setFailure] = useState<string>()
  const [answering, setAnswering] = useState<"finish" | "stop">()

  const live = useRef<LiveBrowserInput>(null)
  const answer = async (pressed: "finish" | "stop", work: () => Promise<unknown>) => {
    setAnswering(pressed)
    try {
      await work()
    } catch (error) {
      setFailure(reason(error))
      setAnswering(undefined)
    }
  }
  const busy = answering !== undefined
  const name = profile.trim()

  return (
    <section aria-label="Take over the browser" className="flex gap-3">
      <InstanceAvatar avatar={speaker.avatar} name={speaker.name} />
      <Card className="min-w-0 flex-1">
        <CardContent>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <h2 className="text-lg font-semibold">Can you take over the browser?</h2>
              <p className="text-sm text-muted-foreground">{takeover.reason}</p>
            </div>
            <LiveBrowser
              ref={live}
              watch={watch}
              send={(input) => hub.call("thread.takeover.input", { ...page, input })}
              // Input after Finish or Stop would reach a turn that already moved on.
              disabled={busy}
            />
            {failure !== undefined && (
              <p role="alert" className="text-sm text-destructive">
                {failure}
              </p>
            )}
            <Field>
              <FieldLabel htmlFor={profileId}>Save as a profile</FieldLabel>
              <Input
                id={profileId}
                value={profile}
                placeholder="LinkedIn"
                readOnly={busy}
                onChange={(event) => setProfile(event.target.value)}
              />
              <FieldDescription>
                Name the sign-in to let {speaker.name} open the site signed in later.
              </FieldDescription>
            </Field>
            <div className="flex w-full flex-wrap items-center gap-2">
              <Button
                size="lg"
                className="max-md:h-11"
                disabled={busy}
                onClick={() =>
                  void answer("finish", async () => {
                    // What the user typed last reaches the page before the turn resumes.
                    await live.current?.drained()
                    return hub.call(
                      "thread.takeover.finish",
                      name === "" ? ids : { ...ids, profile: name },
                    )
                  })
                }
              >
                {answering === "finish" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <FlagIcon data-icon="inline-start" />
                )}
                Finish
              </Button>
              <Button
                variant="link"
                className="ml-auto max-md:h-11"
                disabled={busy}
                onClick={() =>
                  void answer("stop", () =>
                    client.call("thread.turn.interrupt", { thread_id: threadId }),
                  )
                }
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
  notifications,
  instanceId,
  speaker,
}: {
  turns: TurnBlock[]
  notifications: Notifications
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
                <Turn
                  turn={turn}
                  notifications={notifications}
                  instanceId={instanceId}
                  speaker={speaker}
                />
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
  notifications,
  instanceId,
  speaker,
}: {
  turn: TurnBlock
  notifications: Notifications
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
            ) : run.kind === "notification" ? (
              <NotificationCard key={run.key} step={run.step} notifications={notifications} />
            ) : run.kind === "view" ? (
              <ViewChangedCard key={run.key} step={run.step} instanceId={instanceId} />
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
  | { kind: "notification"; key: string; step: NotificationStep }
  | { kind: "view"; key: string; step: ViewStep }
  | { kind: "rail"; key: string; steps: ToolStep[] }

/** A turn's steps with each stretch of consecutive tool calls gathered into one rail. */
function railed(steps: TurnBlock["steps"]): Run[] {
  const runs: Run[] = []
  steps.forEach((step, index) => {
    const last = runs.at(-1)
    if (step.kind === "text") runs.push({ kind: "text", key: `text-${index}`, text: step.text })
    else if (step.kind === "notification")
      runs.push({ kind: "notification", key: `notification-${step.raised}`, step })
    else if (step.kind === "view") runs.push({ kind: "view", key: `view-${index}`, step })
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
 * A notification the turn raised: what it said, where it stands, and its options. The hub takes an
 * answer once it holds the notification, and the user's answer takes the options' place.
 */
function NotificationCard({
  step,
  notifications,
}: {
  step: NotificationStep
  notifications: Notifications
}) {
  const titleId = useId()
  const [answering, setAnswering] = useState<string>()
  const [failure, setFailure] = useState<string>()
  const notificationId = step.hub?.kind === "accepted" ? step.hub.notificationId : undefined
  const kept = notificationId === undefined ? undefined : notifications.kept.get(notificationId)
  const replies = kept?.replies.filter((reply) => reply.option != null) ?? []
  const applied = replies.find((reply) => reply.status === "applied")
  const latest = replies.at(-1)
  // An answer the hub could not apply leaves the options to answer again.
  const unapplied =
    latest?.status === "failed" || latest?.status === "stale" ? latest.outcome : undefined
  const shown = failure ?? unapplied ?? undefined
  const status = notificationStatus(step, kept)
  const settled =
    step.approval?.settled === undefined ? undefined : SETTLED_APPROVALS[step.approval.settled]

  const answer = async (id: string, option: string) => {
    setAnswering(option)
    try {
      await notifications.answer(id, option)
      setFailure(undefined)
    } catch (error) {
      setFailure(reason(error))
    } finally {
      setAnswering(undefined)
    }
  }

  return (
    <article aria-labelledby={titleId}>
      <Card size="sm">
        <CardHeader>
          <CardTitle id={titleId}>
            <span className="flex items-center gap-2">
              <BellIcon aria-hidden className="size-4 shrink-0" />
              {step.title}
            </span>
          </CardTitle>
          {status !== undefined && (
            <CardAction>
              <Badge variant={status.variant}>{status.label}</Badge>
            </CardAction>
          )}
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-3">
            <p className="whitespace-pre-wrap">{step.text}</p>
            {settled !== undefined ? (
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <settled.icon aria-hidden className="size-3.5" />
                {settled.label}
              </p>
            ) : applied !== undefined ? (
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <CheckIcon aria-hidden className="size-3.5" />
                {`Answered “${applied.option}”`}
              </p>
            ) : latest?.status === "received" ? (
              <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <Spinner aria-hidden className="size-3.5" />
                {`Answering “${latest.option}”`}
              </p>
            ) : (
              step.options.length > 0 && (
                <Field data-invalid={shown !== undefined || undefined}>
                  <div className="flex flex-wrap gap-2">
                    {step.options.map((option) => (
                      <Button
                        key={option}
                        size="sm"
                        variant="outline"
                        disabled={kept === undefined || answering !== undefined}
                        onClick={() => {
                          if (kept !== undefined) void answer(kept.notification_id, option)
                        }}
                      >
                        {answering === option && <Spinner data-icon="inline-start" />}
                        {option}
                      </Button>
                    ))}
                  </div>
                  {shown !== undefined && <FieldError>{shown}</FieldError>}
                </Field>
              )
            )}
          </div>
        </CardContent>
      </Card>
    </article>
  )
}

/** What an approval's notification shows in place of its options once the approval settled. */
const SETTLED_APPROVALS = {
  allow: { icon: CheckIcon, label: "Approved" },
  deny: { icon: BanIcon, label: "Denied" },
  ended: { icon: CircleStopIcon, label: "No longer waiting" },
} satisfies Record<string, { icon: LucideIcon; label: string }>

/**
 * Where a notification stands: waiting for the hub, refused by it, or how its channels took it.
 * One the hub took shows nothing until the hub tells.
 */
function notificationStatus(
  step: NotificationStep,
  kept: Notification | undefined,
): { label: string; variant: "outline" | "secondary" | "destructive" } | undefined {
  if (step.hub === undefined) return { label: "Waiting for the hub", variant: "secondary" }
  if (step.hub.kind === "refused") {
    return { label: `Not sent: ${step.hub.reason}`, variant: "destructive" }
  }
  if (kept === undefined) return undefined
  const statuses = new Set(kept.deliveries.map((delivery) => delivery.status))
  if (statuses.has("sent")) return { label: "Delivered", variant: "outline" }
  if (statuses.has("pending")) return { label: "Sending", variant: "secondary" }
  if (statuses.has("failed")) return { label: "Not delivered", variant: "destructive" }
  if (statuses.has("muted")) return { label: "Muted", variant: "outline" }
  return { label: "No channel", variant: "outline" }
}

/** A view the turn created or changed, linking to its page, or one it deleted, struck through. */
function ViewChangedCard({ step, instanceId }: { step: ViewStep; instanceId: string }) {
  const titleId = useId()
  if (step.change === "deleted") {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <LayoutDashboardIcon aria-hidden className="size-4 shrink-0" />
        <del>{step.title}</del>
      </p>
    )
  }
  return (
    <article aria-labelledby={titleId}>
      <Card size="sm">
        <CardHeader>
          <CardTitle id={titleId}>
            <a
              className="flex items-center gap-2"
              href={viewPath(instanceId, step.view)}
              onClick={(event) => {
                event.preventDefault()
                openView(instanceId, step.view)
              }}
            >
              <LayoutDashboardIcon aria-hidden className="size-4 shrink-0" />
              {step.title}
            </a>
          </CardTitle>
          <CardAction>
            <Badge variant="outline">{step.change === "created" ? "Created" : "Changed"}</Badge>
          </CardAction>
        </CardHeader>
      </Card>
    </article>
  )
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
  const tone = waiting
    ? "bg-primary text-primary-foreground"
    : step.result?.error === undefined
      ? "bg-muted text-muted-foreground"
      : "bg-destructive/10 text-destructive"
  return (
    <li className="flex gap-3">
      <span className="relative flex w-5 shrink-0 justify-center">
        {!last && <span aria-hidden className="absolute top-6 bottom-0 w-px bg-border" />}
        <span aria-hidden className={`mt-0.5 grid size-5 place-items-center rounded-full ${tone}`}>
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
