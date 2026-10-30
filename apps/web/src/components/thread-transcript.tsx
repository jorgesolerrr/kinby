import type { Avatar as AvatarChoice } from "@kinby/contract"
import { type ComponentProps, useEffect, useRef } from "react"
import { code } from "@streamdown/code"
import { defaultRehypePlugins, type ExtraProps, Streamdown, type StreamdownProps } from "streamdown"

import { InstanceAvatar } from "@/components/instance-avatar"
import { NotificationCard, ViewChangedCard } from "@/components/thread-cards"
import { EndMarker, RecapMarker } from "@/components/thread-markers"
import { ToolRun } from "@/components/thread-tool-run"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
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
import type { NotificationStep, ToolStep, TurnBlock, ViewStep } from "@/lib/timeline"
import { FactoryIcon, HouseIcon, RepeatIcon, UserIcon, type LucideIcon } from "lucide-react"

/** The instance as the transcript and the approval panel show it. */
export interface Speaker {
  name: string
  avatar: AvatarChoice
}

export function Transcript({
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
            <Bubble align="end" variant="secondary">
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
              <Bubble key={run.key} variant="outline">
                <BubbleContent>
                  <Reply text={run.text} />
                </BubbleContent>
              </Bubble>
            ) : run.kind === "notification" ? (
              <NotificationCard key={run.key} step={run.step} notifications={notifications} />
            ) : run.kind === "view" ? (
              <ViewChangedCard key={run.key} step={run.step} instanceId={instanceId} />
            ) : (
              <ToolRun key={run.key} steps={run.steps} approval={turn.approval} end={turn.end} />
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
      className="grid size-5 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground"
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
