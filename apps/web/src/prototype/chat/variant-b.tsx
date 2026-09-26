// PROTOTYPE, throwaway. Variant B: the conversation stays clean (only what was said), and the
// work moves to a rail on the right: every tool call of the selected turn with its gate decision.
// A pending approval docks above the composer. Threads live in a header menu; the mode is a
// toggle in the header, always visible.
import type { PermissionMode } from "@kinby/contract"
import { useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Message, MessageContent, MessageFooter } from "@/components/ui/message"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Separator } from "@/components/ui/separator"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { ChevronDownIcon, ClockIcon, ListTreeIcon, PlusIcon, ShieldAlertIcon } from "lucide-react"

import { type Chat, threadStatus, threadTitle, type ToolStep, type TurnView } from "./flow"
import { ApprovalActions, Composer, mainArg, ToolLine, TranscriptSkeleton, TurnEnd } from "./parts"
import { INSTANCE, MODES } from "./stub"

export const name = "Clean chat + work rail"

export function VariantB({ chat }: { chat: Chat }) {
  const [focus, setFocus] = useState<string>()
  const turn = chat.view.turns.find((t) => t.turnId === focus) ?? chat.view.turns.at(-1)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Header chat={chat} />
      <Separator />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <Transcript chat={chat} focus={turn?.turnId} onFocus={setFocus} />
          <div className="mx-auto flex w-full max-w-2xl flex-col gap-2 p-4">
            {chat.view.pending && <ApprovalDock step={chat.view.pending.step} chat={chat} />}
            <Composer chat={chat} />
          </div>
        </div>
        <Separator orientation="vertical" />
        <WorkRail turn={turn} />
      </div>
    </div>
  )
}

function Header({ chat }: { chat: Chat }) {
  const attention = chat.threads.filter(
    (t) => t.id !== chat.thread.id && threadStatus(t) === "approval",
  ).length
  return (
    <div className="flex items-center gap-2 px-4 py-2">
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" />}>
          {threadTitle(chat.thread)}
          <ChevronDownIcon data-icon="inline-end" />
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-72">
          <DropdownMenuGroup>
            <DropdownMenuItem onClick={chat.createThread}>
              <PlusIcon />
              New thread
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            {chat.threads.map((t) => (
              <DropdownMenuItem key={t.id} onClick={() => chat.select(t.id)}>
                <span className="truncate">{threadTitle(t)}</span>
                {threadStatus(t) === "approval" && <Badge className="ml-auto">needs you</Badge>}
                {threadStatus(t) === "running" && (
                  <Badge variant="secondary" className="ml-auto">
                    working
                  </Badge>
                )}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      {attention > 0 && <Badge>{attention} other thread needs you</Badge>}
      <ToggleGroup
        className="ml-auto"
        size="sm"
        variant="outline"
        value={[chat.mode]}
        onValueChange={(v: string[]) => v[0] && chat.setMode(v[0] as PermissionMode)}
      >
        {MODES.filter((m) => INSTANCE.allowedModes.includes(m.mode)).map((m) => (
          <ToggleGroupItem key={m.mode} value={m.mode} title={m.hint}>
            {m.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  )
}

function Transcript({
  chat,
  focus,
  onFocus,
}: {
  chat: Chat
  focus?: string
  onFocus: (turnId: string) => void
}) {
  if (!chat.sub.replayed) return <TranscriptSkeleton />
  if (chat.view.turns.length === 0)
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>New thread</EmptyTitle>
          <EmptyDescription>What should {INSTANCE.name} do?</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="end">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="mx-auto max-w-2xl p-6">
            {chat.view.turns.map((turn) => (
              <Turn
                key={turn.turnId}
                turn={turn}
                focused={turn.turnId === focus}
                onFocus={() => onFocus(turn.turnId)}
              />
            ))}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  )
}

function Turn({ turn, focused, onFocus }: { turn: TurnView; focused: boolean; onFocus: () => void }) {
  const text = turn.steps.flatMap((s) => (s.kind === "text" ? [s.text] : [])).join("")
  const tools = turn.steps.filter((s) => s.kind === "tool").length
  return (
    <>
      <MessageScrollerItem messageId={`${turn.turnId}-user`} scrollAnchor>
        {turn.origin.kind === "routine" ? (
          <Marker variant="separator">
            <MarkerIcon>
              <ClockIcon />
            </MarkerIcon>
            <MarkerContent>Routine {turn.origin.name}</MarkerContent>
          </Marker>
        ) : (
          <Message align="end">
            <MessageContent>
              <Bubble align="end" variant="secondary">
                <BubbleContent>{turn.message}</BubbleContent>
              </Bubble>
            </MessageContent>
          </Message>
        )}
      </MessageScrollerItem>
      <MessageScrollerItem messageId={`${turn.turnId}-agent`}>
        <Message>
          <MessageContent>
            {text && (
              <Bubble variant="ghost">
                <BubbleContent>{text}</BubbleContent>
              </Bubble>
            )}
            <TurnEnd turn={turn} />
            <MessageFooter>
              <Button size="xs" variant={focused ? "secondary" : "ghost"} onClick={onFocus}>
                <ListTreeIcon data-icon="inline-start" />
                {tools} {tools === 1 ? "step" : "steps"}
              </Button>
            </MessageFooter>
          </MessageContent>
        </Message>
      </MessageScrollerItem>
    </>
  )
}

function WorkRail({ turn }: { turn?: TurnView }) {
  const tools = turn?.steps.filter((s): s is ToolStep => s.kind === "tool") ?? []
  return (
    <div className="flex w-96 shrink-0 flex-col gap-2 overflow-y-auto p-3">
      <span className="text-xs font-medium text-muted-foreground">
        {turn ? `Steps · ${turn.message.slice(0, 40)}` : "Steps"}
      </span>
      {tools.length === 0 && (
        <span className="text-xs text-muted-foreground">No tool calls in this turn.</span>
      )}
      {tools.map((step) => (
        <ToolLine key={step.callId} step={step} />
      ))}
    </div>
  )
}

function ApprovalDock({ step, chat }: { step: ToolStep; chat: Chat }) {
  return (
    <Alert>
      <ShieldAlertIcon />
      <AlertTitle>
        Approve {step.name} on {mainArg(step)}?
      </AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <span>{step.approval?.rule}. The turn waits until you answer.</span>
        <ApprovalActions chat={chat} withNote />
      </AlertDescription>
    </Alert>
  )
}
