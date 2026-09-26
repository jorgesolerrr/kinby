// PROTOTYPE, throwaway. Variant C: threads nest under the instance in the app sidebar (see
// index.tsx). Each turn reads as a timeline: the request, then every step in order with its gate
// decision as a marker, then the outcome. A pending approval takes the composer's place, so the
// only way forward is to answer it or stop the turn.
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
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
import { Separator } from "@/components/ui/separator"
import {
  BanIcon,
  CheckIcon,
  CircleStopIcon,
  ClockIcon,
  ShieldAlertIcon,
  UserIcon,
} from "lucide-react"

import { type Chat, threadTitle, type ToolStep, type TurnView } from "./flow"
import { ApprovalActions, Composer, mainArg, ModeSelect, toolIcon, TranscriptSkeleton, TurnEnd } from "./parts"
import { INSTANCE } from "./stub"

export const name = "Sidebar threads + turn timeline"

export function VariantC({ chat }: { chat: Chat }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-4 py-2">
        <span className="font-medium">{threadTitle(chat.thread)}</span>
        <span className="ml-auto text-xs text-muted-foreground">Mode</span>
        <ModeSelect chat={chat} />
      </div>
      <Separator />
      <Transcript chat={chat} />
      <div className="mx-auto w-full max-w-3xl p-4">
        {chat.view.pending ? (
          <ApprovalBar step={chat.view.pending.step} chat={chat} />
        ) : (
          <Composer chat={chat} />
        )}
      </div>
    </div>
  )
}

function Transcript({ chat }: { chat: Chat }) {
  if (!chat.sub.replayed) return <TranscriptSkeleton />
  if (chat.view.turns.length === 0)
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>No turns yet</EmptyTitle>
          <EmptyDescription>Each request you send becomes a turn with its steps.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="mx-auto max-w-3xl gap-10 p-6">
            {chat.view.turns.map((turn) => (
              <MessageScrollerItem key={turn.turnId} messageId={turn.turnId} scrollAnchor>
                <TurnBlock turn={turn} />
              </MessageScrollerItem>
            ))}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  )
}

function TurnBlock({ turn }: { turn: TurnView }) {
  return (
    <div className="flex flex-col gap-3">
      <Marker variant="border">
        <MarkerIcon>{turn.origin.kind === "routine" ? <ClockIcon /> : <UserIcon />}</MarkerIcon>
        <MarkerContent>
          {turn.origin.kind === "routine" ? `Routine ${turn.origin.name}` : "You"} ·{" "}
          {new Date(turn.startedAt).toLocaleTimeString()}
        </MarkerContent>
      </Marker>
      <p className="text-base font-medium">{turn.message}</p>
      <Message>
        <MessageContent>
          <MessageHeader>{INSTANCE.name}</MessageHeader>
          {turn.steps.map((step, i) =>
            step.kind === "text" ? (
              <Bubble key={i} variant="ghost">
                <BubbleContent>{step.text}</BubbleContent>
              </Bubble>
            ) : (
              <StepMarker key={step.callId} step={step} />
            ),
          )}
          <TurnEnd turn={turn} />
          {turn.end?.kind === "completed" && (
            <Marker>
              <MarkerIcon>
                <CheckIcon />
              </MarkerIcon>
              <MarkerContent>
                Done · {turn.steps.filter((s) => s.kind === "tool").length} steps ·{" "}
                {turn.end.tokens.toLocaleString()} tokens
              </MarkerContent>
            </Marker>
          )}
        </MessageContent>
      </Message>
    </div>
  )
}

function StepMarker({ step }: { step: ToolStep }) {
  const icon =
    step.gate?.action === "deny" ? (
      <BanIcon />
    ) : step.approval && !step.gate ? (
      <ShieldAlertIcon />
    ) : (
      toolIcon(step)
    )
  const verdict = !step.gate
    ? step.approval
      ? "waiting for you"
      : "running"
    : step.gate.action === "deny"
      ? `denied by ${step.gate.decidedBy === "user" ? "you" : "policy"}: ${step.gate.rule}`
      : step.gate.decidedBy === "user"
        ? "approved by you"
        : step.result
          ? `${step.result.durationMs ?? 0} ms`
          : "running"
  return (
    <Marker>
      <MarkerIcon>{icon}</MarkerIcon>
      <MarkerContent>
        <span className="font-mono text-foreground">{step.name}</span> {mainArg(step)} · {verdict}
      </MarkerContent>
    </Marker>
  )
}

function ApprovalBar({ step, chat }: { step: ToolStep; chat: Chat }) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border p-3">
      <div className="flex items-center gap-2">
        <ShieldAlertIcon />
        <span className="font-medium">
          {step.name} {mainArg(step)}
        </span>
        <Badge variant="outline">{step.approval?.rule}</Badge>
      </div>
      <pre className="max-h-32 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
        {JSON.stringify(step.args, null, 2)}
      </pre>
      <div className="flex items-center gap-2">
        <ApprovalActions chat={chat} />
        <Button size="sm" variant="ghost" className="ml-auto" onClick={chat.interrupt}>
          <CircleStopIcon data-icon="inline-start" />
          Stop the turn
        </Button>
      </div>
    </div>
  )
}
