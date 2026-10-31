import type { Avatar as AvatarChoice } from "@kinby/contract"
import { type ComponentProps, useEffect, useRef } from "react"
import { code } from "@streamdown/code"
import { defaultRehypePlugins, type ExtraProps, Streamdown, type StreamdownProps } from "streamdown"

import { RaisedNotification, ViewLine } from "@/components/thread-cards"
import { EndMarker, RecapMarker } from "@/components/thread-markers"
import { ToolRun } from "@/components/thread-tool-run"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Message, MessageContent } from "@/components/ui/message"
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
import type { Notifications } from "@/hooks/use-notifications"
import { originLabel } from "@/lib/origin"
import { navigate, useSelectedTurnId } from "@/lib/selection"
import {
  localDay,
  type NotificationStep,
  type ToolStep,
  type TurnBlock,
  type ViewStep,
} from "@/lib/timeline"
import { FactoryIcon, HouseIcon, RepeatIcon, type LucideIcon } from "lucide-react"

/** The instance as the approval and takeover panels show it. */
export interface Speaker {
  name: string
  avatar: AvatarChoice
}

export function Transcript({
  turns,
  notifications,
  instanceId,
}: {
  turns: TurnBlock[]
  notifications: Notifications
  instanceId: string
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
            {turns.map((turn, index) => (
              <MessageScrollerItem key={turn.turnId} messageId={turn.turnId} scrollAnchor>
                <Turn
                  turn={turn}
                  previous={turns[index - 1]}
                  notifications={notifications}
                  instanceId={instanceId}
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

/** What started a turn the user did not: the instance acting on its own. */
const STARTED_BY_ICONS: Record<Exclude<TurnBlock["startedBy"]["kind"], "user">, LucideIcon> = {
  routine: RepeatIcon,
  "factory-run": FactoryIcon,
  "home-instance": HouseIcon,
}

/**
 * The day the turn starts on, when the turn before it started on another. Then the user's request
 * in a bubble on the right, or a line naming what started the turn on its own. Then the
 * instance's replies as prose across the column, and the tool calls between them as one rail of
 * steps.
 */
function Turn({
  turn,
  previous,
  notifications,
  instanceId,
}: {
  turn: TurnBlock
  previous: TurnBlock | undefined
  notifications: Notifications
  instanceId: string
}) {
  const { startedBy } = turn
  const thinking = turn.end === undefined && turn.steps.length === 0
  return (
    <div className="flex flex-col gap-4 px-6">
      {turn.startedOn !== previous?.startedOn && (
        <Marker variant="separator">
          <MarkerContent>{dayLabel(turn)}</MarkerContent>
        </Marker>
      )}
      {startedBy.kind === "user" ? (
        <Message align="end">
          <MessageContent>
            <Bubble align="end" variant="secondary">
              <BubbleContent className="whitespace-pre-wrap">{turn.request}</BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      ) : (
        <div className="flex flex-col gap-2">
          <Marker>
            <MarkerIcon>
              <StartedByIcon kind={startedBy.kind} />
            </MarkerIcon>
            <MarkerContent>
              {originLabel(startedBy)} ·{" "}
              <time dateTime={turn.startedAt}>{startedAt(turn.startedAt)}</time>
            </MarkerContent>
          </Marker>
          <p className="pl-6 text-sm whitespace-pre-wrap text-muted-foreground">{turn.request}</p>
        </div>
      )}
      {railed(turn.steps).map((run) =>
        run.kind === "text" ? (
          <Reply key={run.key} text={run.text} />
        ) : run.kind === "notification" ? (
          <RaisedNotification key={run.key} step={run.step} notifications={notifications} />
        ) : run.kind === "view" ? (
          <ViewLine key={run.key} step={run.step} instanceId={instanceId} />
        ) : (
          <ToolRun key={run.key} steps={run.steps} approval={turn.approval} end={turn.end} />
        ),
      )}
      {thinking ? (
        <span className="shimmer text-sm text-muted-foreground">Thinking</span>
      ) : (
        <EndMarker turn={turn} />
      )}
      {turn.recap !== undefined && <RecapMarker recap={turn.recap} instanceId={instanceId} />}
    </div>
  )
}

function StartedByIcon({ kind }: { kind: keyof typeof STARTED_BY_ICONS }) {
  const Icon = STARTED_BY_ICONS[kind]
  return <Icon />
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
  // A reply reads in the serif that marks the instance's voice. Its headings stay below the
  // thread's own title, in kinby's heading face, and none is smaller than the prose.
  className:
    "font-reading text-base leading-7 [&_h1]:text-lg [&_:is(h2,h3,h4)]:text-base [&_:is(h1,h2,h3,h4)]:mt-5 [&_:is(h1,h2,h3,h4)]:font-heading [&_:is(h1,h2,h3,h4)]:font-semibold",
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

/** The day a turn started on, as its divider names it. */
function dayLabel({ startedAt, startedOn }: TurnBlock): string {
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (startedOn === localDay(today.toISOString())) return "Today"
  if (startedOn === localDay(yesterday.toISOString())) return "Yesterday"
  return new Date(startedAt).toLocaleDateString("en", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  })
}

/** The time a turn started, under the divider that names its day. */
function startedAt(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString("en", {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })
}

export function TranscriptSkeleton() {
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
