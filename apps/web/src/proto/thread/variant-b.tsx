// PROTOTYPE: variant B · Work log. The Codex and Claude Code shape: the thread is one timeline
// down a left rail, every tool call a visible line you can open, who spoke named at each step.
// Sessions and turn ends are explicit markers. An approval or a takeover takes the composer's
// place until it is answered, and an instance notice sits in the stream.
import {
  ArrowUpIcon,
  BellIcon,
  ChevronDownIcon,
  CircleStopIcon,
  FileImageIcon,
  FileTextIcon,
  LayoutDashboardIcon,
  MousePointerClickIcon,
  PaperclipIcon,
  RefreshCwIcon,
  RepeatIcon,
  ShieldAlertIcon,
  TriangleAlertIcon,
} from "lucide-react"
import type * as React from "react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import {
  Attachment,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Kbd } from "@/components/ui/kbd"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Spinner } from "@/components/ui/spinner"
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace"
import { cn } from "cn"

import { INSTANCES, type MockInstance, instanceById } from "../shell/mock"
import { MODES, NOTICE, type Step, type Tool, type Turn } from "./mock"
import { Reply, seconds, toolIcon } from "./shared"
import type { ThreadProps } from "./variant-a"

export function ThreadB({ instance, turns, streaming, notice }: ThreadProps) {
  const last = turns.at(-1)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <MessageScrollerProvider autoScroll defaultScrollPosition="end">
        <MessageScroller className="min-h-0 flex-1">
          <MessageScrollerViewport>
            <MessageScrollerContent className="mx-auto w-full max-w-3xl gap-0 px-4 pt-6 pb-10">
              {turns.length === 0 && <NewThread instance={instance} />}
              {turns.map((turn, i) => (
                <MessageScrollerItem key={turn.id} messageId={turn.id} scrollAnchor>
                  <TurnB
                    turn={turn}
                    instance={instance}
                    live={i === turns.length - 1 && streaming}
                  />
                </MessageScrollerItem>
              ))}
              {notice && (
                <MessageScrollerItem messageId="notice">
                  <Row icon={<TriangleAlertIcon className="text-ember" />}>
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span>{NOTICE.title}</span>
                      <Button size="xs" variant="outline">
                        {NOTICE.action}
                      </Button>
                    </div>
                  </Row>
                </MessageScrollerItem>
              )}
            </MessageScrollerContent>
          </MessageScrollerViewport>
          <MessageScrollerButton />
        </MessageScroller>
      </MessageScrollerProvider>
      <div className="border-t bg-background">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          {last?.approval ? (
            <ApprovalB approval={last.approval} />
          ) : last?.takeover ? (
            <TakeoverB instance={instance} takeover={last.takeover} />
          ) : (
            <ComposerB instanceId={instance.id} working={streaming} />
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * One line of the timeline: a node on the rail, then its content. The rail runs through every
 * row, so a turn reads as one sequence top to bottom.
 */
function Row({
  icon,
  children,
  className,
}: {
  icon: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("relative flex gap-3 pb-4", className)}>
      <div className="absolute top-7 bottom-0 left-3.5 w-px bg-border" aria-hidden />
      <div className="relative flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground [&_svg:not([class*='size-'])]:size-4">
        {icon}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 pt-1">{children}</div>
    </div>
  )
}

function Who({ name, at }: { name: string; at?: string }) {
  return (
    <div className="flex items-baseline gap-2 text-sm">
      <span className="font-medium">{name}</span>
      {at && <span className="text-xs text-muted-foreground tabular-nums">{at}</span>}
    </div>
  )
}

function You() {
  return (
    <Avatar size="sm">
      <AvatarFallback>J</AvatarFallback>
    </Avatar>
  )
}

function NewThread({ instance }: { instance: MockInstance }) {
  return (
    <MessageScrollerItem messageId="first" scrollAnchor>
      <Row icon={<You />}>
        <Who name="You" at="now" />
        <p className="text-sm leading-6">
          What's on my calendar this week that I haven't prepared for?
        </p>
      </Row>
      <Row icon={<InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />}>
        <Who name={instance.name} />
        <span className="shimmer text-sm text-muted-foreground">Starting a session…</span>
      </Row>
    </MessageScrollerItem>
  )
}

function TurnB({ turn, instance, live }: { turn: Turn; instance: MockInstance; live: boolean }) {
  return (
    <div className="flex flex-col">
      {turn.session && (
        <Marker variant="separator" className="py-4">
          <MarkerContent>
            New session · {turn.session.day} {turn.session.time}
          </MarkerContent>
        </Marker>
      )}
      {turn.by.kind === "user" ? (
        <Row icon={<You />}>
          <Who name="You" at={turn.at} />
          <p className="text-sm leading-6">{turn.by.text}</p>
          {turn.by.attachments && (
            <AttachmentGroup>
              {turn.by.attachments.map((f) => (
                <Attachment key={f.name} size="xs">
                  <AttachmentMedia>
                    {f.image ? <FileImageIcon /> : <FileTextIcon />}
                  </AttachmentMedia>
                  <AttachmentContent>
                    <AttachmentTitle>{f.name}</AttachmentTitle>
                    <AttachmentDescription>{f.meta}</AttachmentDescription>
                  </AttachmentContent>
                </Attachment>
              ))}
            </AttachmentGroup>
          )}
        </Row>
      ) : (
        <Row icon={<RepeatIcon />}>
          <Who name={`Routine · ${turn.by.name}`} at={turn.at} />
          <p className="text-sm text-muted-foreground">Started on its schedule.</p>
        </Row>
      )}
      {turn.steps.map((step, i) => (
        <StepB
          key={i}
          step={step}
          instance={instance}
          live={live && i === turn.steps.length - 1}
          first={i === turn.steps.findIndex((s) => s.kind === "text")}
        />
      ))}
      {live && turn.steps.length === 0 && (
        <Row icon={<Spinner />}>
          <span className="shimmer text-sm text-muted-foreground">Thinking</span>
        </Row>
      )}
      <EndB turn={turn} live={live} />
    </div>
  )
}

function StepB({
  step,
  instance,
  live,
  first,
}: {
  step: Step
  instance: MockInstance
  live: boolean
  first: boolean
}) {
  switch (step.kind) {
    case "text":
      return (
        <Row icon={<InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />}>
          {first && <Who name={instance.name} />}
          <Reply text={step.text} streaming={live} />
        </Row>
      )
    case "tools":
      return (
        <>
          {step.tools.map((tool) => (
            <ToolRowB key={tool.id} tool={tool} />
          ))}
        </>
      )
    case "view":
      return (
        <Row icon={<LayoutDashboardIcon />}>
          <Card size="sm" className="py-2.5">
            <CardHeader className="px-3">
              <CardTitle className="text-sm">{step.title}</CardTitle>
              <CardDescription>A view of {instance.name}'s</CardDescription>
              <CardAction>
                <Badge variant="secondary">{step.change}</Badge>
              </CardAction>
            </CardHeader>
          </Card>
        </Row>
      )
    case "notification":
      return (
        <Row icon={<BellIcon />}>
          <div className="flex flex-wrap items-baseline gap-2 text-sm">
            <span className="font-medium">{step.title}</span>
            <span className="text-muted-foreground">{step.text}</span>
            <Badge variant="outline">{step.delivered}</Badge>
          </div>
        </Row>
      )
  }
}

/** Every call is its own row, its arguments and output a click away. */
function ToolRowB({ tool }: { tool: Tool }) {
  const Icon = toolIcon(tool.name)
  const status = tool.running ? "running" : tool.error ? "error" : undefined
  return (
    <Row
      icon={tool.running ? <Spinner /> : <Icon />}
      className={cn(
        tool.parked && "[&>div:nth-child(2)]:bg-ember/20 [&>div:nth-child(2)]:text-foreground",
      )}
    >
      <Trace>
        <TraceCollapsible>
          <TraceCollapsibleTrigger>
            <TraceLine status={status}>
              <TraceTitle className="font-mono text-xs">
                <span className="text-foreground">{tool.name}</span> {tool.arg}
              </TraceTitle>
              <TraceDescription className="text-xs">
                {tool.running
                  ? "running"
                  : tool.parked
                    ? "waiting for you"
                    : tool.error
                      ? `failed after ${seconds(tool.ms ?? 0)}: ${tool.error}`
                      : [tool.gate, tool.ms !== undefined && seconds(tool.ms)]
                          .filter(Boolean)
                          .join(" · ")}
              </TraceDescription>
            </TraceLine>
          </TraceCollapsibleTrigger>
          <TraceCollapsibleContent className="flex flex-col gap-2 font-mono text-xs">
            <div>
              <div className="text-muted-foreground/70">input</div>
              <div>{tool.input ?? `{ "target": "${tool.arg}" }`}</div>
            </div>
            {tool.output && (
              <div>
                <div className="text-muted-foreground/70">output</div>
                <div className="text-foreground">{tool.output}</div>
              </div>
            )}
          </TraceCollapsibleContent>
        </TraceCollapsible>
      </Trace>
    </Row>
  )
}

function EndB({ turn, live }: { turn: Turn; live: boolean }) {
  const end = turn.end
  if (end === undefined)
    return live || turn.approval || turn.takeover ? (
      <Marker className="pb-6 pl-10">
        <Spinner />
        <MarkerContent className="shimmer">
          {turn.approval
            ? "Waiting for your approval"
            : turn.takeover
              ? "Waiting for you"
              : "Working"}
        </MarkerContent>
      </Marker>
    ) : null
  if (end.kind === "failed")
    return (
      <Marker variant="destructive" className="pb-6 pl-10">
        <MarkerIcon>
          <TriangleAlertIcon />
        </MarkerIcon>
        <MarkerContent>
          Failed: {end.message} ({end.code})
        </MarkerContent>
        <Button size="xs" variant="outline" className="ml-auto">
          <RefreshCwIcon data-icon="inline-start" />
          Retry
        </Button>
      </Marker>
    )
  if (end.kind === "stopped")
    return (
      <Marker className="pb-6 pl-10">
        <MarkerContent>Stopped</MarkerContent>
      </Marker>
    )
  return (
    <Marker className="pb-6 pl-10">
      <MarkerContent>
        Done · {end.steps} steps · {end.tokens} tokens · {end.took}
        {turn.recapped && " · recapped"}
      </MarkerContent>
    </Marker>
  )
}

function ApprovalB({ approval }: { approval: NonNullable<Turn["approval"]> }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-ember/60 bg-card p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <ShieldAlertIcon className="size-4 text-ember" />
        <span className="font-mono">{approval.tool}</span> needs your approval
        <span className="ml-auto text-xs font-normal text-muted-foreground">{approval.rule}</span>
      </div>
      <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
        {approval.input}
        {"\n\n"}
        {approval.preview}
      </pre>
      <Input placeholder="Reason, if you deny (optional)" />
      <div className="flex flex-wrap gap-2">
        <Button size="sm">
          Approve <Kbd>Enter</Kbd>
        </Button>
        <Button size="sm" variant="outline">
          Always allow in this routine
        </Button>
        <Button size="sm" variant="outline">
          Mark read-only
        </Button>
        <Button size="sm" variant="outline">
          Deny
        </Button>
        <Button size="sm" variant="ghost" className="ml-auto">
          <CircleStopIcon data-icon="inline-start" />
          Stop the turn
        </Button>
      </div>
    </div>
  )
}

function TakeoverB({
  instance,
  takeover,
}: {
  instance: MockInstance
  takeover: NonNullable<Turn["takeover"]>
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-ember/60 bg-card p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <MousePointerClickIcon className="size-4 text-ember" />
        Take over {instance.name}'s browser
      </div>
      <p className="text-sm text-muted-foreground">{takeover.reason}</p>
      <div className="flex aspect-video max-h-48 items-center justify-center rounded-md border border-dashed bg-muted text-sm text-muted-foreground">
        Live browser · {takeover.site}
      </div>
      <div className="flex gap-2">
        <Button size="sm">I'm done</Button>
        <Button size="sm" variant="ghost" className="ml-auto">
          <CircleStopIcon data-icon="inline-start" />
          Stop the turn
        </Button>
      </div>
    </div>
  )
}

/**
 * The composer: a flat bar docked under the timeline. A strip of chips above the text names
 * where it goes and under what mode; files attach from the paperclip.
 */
export function ComposerB({
  instanceId,
  working,
  pick,
}: {
  instanceId: string
  working?: boolean
  pick?: boolean
}) {
  const [to, setTo] = useState(instanceId)
  const [mode, setMode] = useState("ask")
  const [files, setFiles] = useState<string[]>([])
  const instance = instanceById(to)
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <DropdownMenu>
          <DropdownMenuTrigger disabled={!pick} render={<Button size="xs" variant="outline" />}>
            <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
            {instance.name}
            {pick && <ChevronDownIcon data-icon="inline-end" />}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuGroup>
              <DropdownMenuLabel>Send to</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={to} onValueChange={(v: string) => setTo(v)}>
                {INSTANCES.filter((i) => i.state === "running").map((i) => (
                  <DropdownMenuRadioItem key={i.id} value={i.id}>
                    {i.name}
                    {i.home && " (home)"}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="xs" variant="outline" />}>
            {MODES.find((m) => m.value === mode)?.label} mode
            <ChevronDownIcon data-icon="inline-end" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuGroup>
              <DropdownMenuLabel>Permission mode</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={mode} onValueChange={(v: string) => setMode(v)}>
                {MODES.map((m) => (
                  <DropdownMenuRadioItem key={m.value} value={m.value}>
                    {m.label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        {files.map((f) => (
          <Badge key={f} variant="secondary">
            <PaperclipIcon data-icon="inline-start" />
            {f}
          </Badge>
        ))}
      </div>
      <InputGroup className="h-auto">
        <InputGroupTextarea
          variant="prompt"
          placeholder={working ? `${instance.name} is working…` : `Ask ${instance.name} to…`}
          className="min-h-11"
        />
        <InputGroupAddon align="inline-end" className="self-end">
          <InputGroupButton
            size="icon-xs"
            aria-label="Attach files"
            onClick={() => setFiles(["lease-2026.pdf"])}
          >
            <PaperclipIcon />
          </InputGroupButton>
          {working ? (
            <InputGroupButton variant="secondary" size="xs">
              Stop <Kbd>Esc</Kbd>
            </InputGroupButton>
          ) : (
            <InputGroupButton variant="default" size="icon-xs" aria-label="Send">
              <ArrowUpIcon />
            </InputGroupButton>
          )}
        </InputGroupAddon>
      </InputGroup>
    </div>
  )
}
