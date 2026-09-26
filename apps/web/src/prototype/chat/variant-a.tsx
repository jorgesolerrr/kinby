// PROTOTYPE, throwaway. Variant A: a thread column beside a classic chat column. Tool calls fold
// into the assistant's message as lines; an approval is a card in the stream, at the call it gates.
// The mode picker sits in the composer.
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Message, MessageContent, MessageFooter, MessageHeader } from "@/components/ui/message"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Separator } from "@/components/ui/separator"
import { ClockIcon, PlusIcon, ShieldAlertIcon } from "lucide-react"

import { type Chat, threadStatus, threadTitle, type ToolStep, type TurnView } from "./flow"
import { ApprovalActions, Composer, mainArg, ModeSelect, ToolLine, TranscriptSkeleton, TurnEnd } from "./parts"
import { INSTANCE } from "./stub"

export const name = "Thread column + inline approvals"

export function VariantA({ chat }: { chat: Chat }) {
  return (
    <div className="flex min-h-0 flex-1">
      <ThreadColumn chat={chat} />
      <Separator orientation="vertical" />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Transcript chat={chat} />
        <div className="mx-auto w-full max-w-3xl p-4">
          <Composer chat={chat} modeSlot={<ModeSelect chat={chat} />} />
        </div>
      </div>
    </div>
  )
}

const statusBadge = {
  running: <Badge variant="secondary">working</Badge>,
  approval: <Badge>needs you</Badge>,
  failed: <Badge variant="destructive">failed</Badge>,
  idle: null,
}

function ThreadColumn({ chat }: { chat: Chat }) {
  return (
    <div className="flex w-64 shrink-0 flex-col gap-2 overflow-y-auto p-2">
      <Button variant="outline" size="sm" onClick={chat.createThread}>
        <PlusIcon data-icon="inline-start" />
        New thread
      </Button>
      <ItemGroup>
        {chat.threads.map((t) => (
          <Item
            key={t.id}
            size="sm"
            variant={t.id === chat.thread.id ? "muted" : "default"}
            render={<button type="button" onClick={() => chat.select(t.id)} />}
          >
            <ItemContent>
              <ItemTitle className="truncate">{threadTitle(t)}</ItemTitle>
              <ItemDescription>{new Date(t.createdAt).toLocaleString()}</ItemDescription>
            </ItemContent>
            <ItemActions>{statusBadge[threadStatus(t)]}</ItemActions>
          </Item>
        ))}
      </ItemGroup>
    </div>
  )
}

function Transcript({ chat }: { chat: Chat }) {
  if (!chat.sub.replayed) return <TranscriptSkeleton />
  if (chat.view.turns.length === 0)
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>Start the conversation</EmptyTitle>
          <EmptyDescription>
            {INSTANCE.name} runs in {chat.mode} mode here.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="end">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="mx-auto max-w-3xl p-6">
            {chat.view.turns.map((turn) => (
              <Turn key={turn.turnId} turn={turn} chat={chat} />
            ))}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  )
}

function Turn({ turn, chat }: { turn: TurnView; chat: Chat }) {
  return (
    <>
      <MessageScrollerItem messageId={`${turn.turnId}-user`} scrollAnchor>
        {turn.origin.kind === "routine" ? (
          <Marker variant="separator">
            <MarkerIcon>
              <ClockIcon />
            </MarkerIcon>
            <MarkerContent>
              Routine {turn.origin.name} ({turn.origin.trigger})
            </MarkerContent>
          </Marker>
        ) : (
          <Message align="end">
            <MessageContent>
              <Bubble align="end">
                <BubbleContent>{turn.message}</BubbleContent>
              </Bubble>
            </MessageContent>
          </Message>
        )}
      </MessageScrollerItem>
      <MessageScrollerItem messageId={`${turn.turnId}-agent`}>
        <Message>
          <MessageContent>
            <MessageHeader>{INSTANCE.name}</MessageHeader>
            {turn.steps.map((step, i) =>
              step.kind === "text" ? (
                <Bubble key={i} variant="ghost">
                  <BubbleContent>{step.text}</BubbleContent>
                </Bubble>
              ) : step.approval && !step.gate ? (
                <ApprovalCard key={step.callId} step={step} chat={chat} />
              ) : (
                <ToolLine key={step.callId} step={step} />
              ),
            )}
            <TurnEnd turn={turn} />
            {turn.end?.kind === "completed" && (
              <MessageFooter>{turn.end.tokens.toLocaleString()} tokens</MessageFooter>
            )}
          </MessageContent>
        </Message>
      </MessageScrollerItem>
    </>
  )
}

function ApprovalCard({ step, chat }: { step: ToolStep; chat: Chat }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <ShieldAlertIcon className="inline" /> {INSTANCE.name} wants to run {step.name}
        </CardTitle>
        <CardDescription>
          {mainArg(step)} · {step.approval?.rule}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
          {JSON.stringify(step.args, null, 2)}
        </pre>
      </CardContent>
      <CardFooter>
        <ApprovalActions chat={chat} withNote />
      </CardFooter>
    </Card>
  )
}
