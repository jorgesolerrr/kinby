// PROTOTYPE: variant A · Prose. The Claude desktop shape: the instance's reply is plain serif
// prose across the column, the user's message a grey bubble on the right, each run of tools one
// quiet line that unfolds. Approvals and takeovers ask inside the stream; the composer stays.
import {
  ArrowUpIcon,
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  FileImageIcon,
  FileTextIcon,
  GlobeIcon,
  LayoutDashboardIcon,
  MousePointerClickIcon,
  PaperclipIcon,
  PlusIcon,
  RefreshCwIcon,
  RepeatIcon,
  ShieldAlertIcon,
  SquareIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Message, MessageContent } from "@/components/ui/message"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace"

import { INSTANCES, type MockInstance, instanceById } from "../shell/mock"
import { type Attachment as File, MODES, NOTICE, type Step, type Tool, type Turn } from "./mock"
import { Reply, doing, seconds, summarize, toolIcon } from "./shared"

export interface ThreadProps {
  instance: MockInstance
  turns: Turn[]
  streaming: boolean
  notice: boolean
}

export function ThreadA({ instance, turns, streaming, notice }: ThreadProps) {
  const last = turns.at(-1)
  const working = streaming || last?.approval !== undefined || last?.takeover !== undefined
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <MessageScrollerProvider autoScroll defaultScrollPosition="end">
        <MessageScroller className="min-h-0 flex-1">
          <MessageScrollerViewport>
            <MessageScrollerContent className="mx-auto w-full max-w-3xl gap-10 px-4 pt-6 pb-10">
              {turns.length === 0 ? (
                <NewThread instance={instance} />
              ) : (
                turns.map((turn, i) => (
                  <MessageScrollerItem key={turn.id} messageId={turn.id} scrollAnchor>
                    <TurnA
                      turn={turn}
                      instance={instance}
                      live={i === turns.length - 1 && streaming}
                    />
                  </MessageScrollerItem>
                ))
              )}
            </MessageScrollerContent>
          </MessageScrollerViewport>
          <MessageScrollerButton />
        </MessageScroller>
      </MessageScrollerProvider>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 px-4 pb-4">
        {notice && (
          <Alert>
            <TriangleAlertIcon />
            <AlertTitle>{NOTICE.title}</AlertTitle>
            <AlertAction>
              <Button size="xs" variant="outline">
                {NOTICE.action}
              </Button>
            </AlertAction>
          </Alert>
        )}
        <ComposerA instanceId={instance.id} working={working} />
        <p className="text-center text-xs text-muted-foreground">
          {instance.name} can make mistakes. It asks before anything it can't undo.
        </p>
      </div>
    </div>
  )
}

/** A thread with no turns yet: its first message was just sent. */
function NewThread({ instance }: { instance: MockInstance }) {
  return (
    <MessageScrollerItem messageId="first" scrollAnchor>
      <div className="flex flex-col gap-6">
        <Message align="end">
          <MessageContent>
            <Bubble variant="secondary" align="end">
              <BubbleContent>
                What's on my calendar this week that I haven't prepared for?
              </BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
          <span className="shimmer">Thinking</span>
        </div>
      </div>
    </MessageScrollerItem>
  )
}

function TurnA({ turn, instance, live }: { turn: Turn; instance: MockInstance; live: boolean }) {
  return (
    <div className="group/turn flex flex-col gap-5">
      {turn.session && (
        <Marker variant="separator">
          <MarkerContent>{turn.session.day}</MarkerContent>
        </Marker>
      )}
      {turn.by.kind === "user" ? (
        <Message align="end">
          <MessageContent>
            {turn.by.attachments && <Files files={turn.by.attachments} />}
            <Bubble variant="secondary" align="end">
              <BubbleContent>{turn.by.text}</BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      ) : (
        <Marker>
          <MarkerIcon>
            <RepeatIcon />
          </MarkerIcon>
          <MarkerContent>
            Routine <span className="text-foreground">{turn.by.name}</span> · {turn.at}
          </MarkerContent>
        </Marker>
      )}
      {turn.steps.map((step, i) => (
        <StepA
          key={i}
          step={step}
          live={live && i === turn.steps.length - 1}
          approval={turn.approval}
        />
      ))}
      {turn.takeover && <TakeoverA instance={instance} takeover={turn.takeover} />}
      {live && turn.steps.at(-1)?.kind !== "text" && turn.steps.length === 0 && (
        <span className="shimmer text-sm text-muted-foreground">Thinking</span>
      )}
      <EndA turn={turn} />
    </div>
  )
}

function StepA({
  step,
  live,
  approval,
}: {
  step: Step
  live: boolean
  approval: Turn["approval"]
}) {
  switch (step.kind) {
    case "text":
      return <Reply text={step.text} streaming={live} />
    case "tools":
      return (
        <>
          <ToolsA tools={step.tools} />
          {approval && step.tools.some((t) => t.parked) && <ApprovalA approval={approval} />}
        </>
      )
    case "view":
      return (
        <Marker render={<a href="#view" />}>
          <MarkerIcon>
            <LayoutDashboardIcon />
          </MarkerIcon>
          <MarkerContent>
            {step.change} the view <span className="text-foreground">{step.title}</span>
          </MarkerContent>
        </Marker>
      )
    case "notification":
      return (
        <Marker>
          <MarkerIcon>
            <CheckIcon />
          </MarkerIcon>
          <MarkerContent>
            Notified you: <span className="text-foreground">{step.title}</span> · {step.delivered}
          </MarkerContent>
        </Marker>
      )
  }
}

/** One run of tools: a summary line, unfolded on demand into one line per call. */
function ToolsA({ tools }: { tools: Tool[] }) {
  const running = tools.find((t) => t.running)
  const parked = tools.find((t) => t.parked)
  const failed = tools.filter((t) => t.error).length
  const title = running
    ? doing(running)
    : parked
      ? `Waiting for you to approve ${parked.name}`
      : summarize(tools)
  return (
    <Trace>
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine status={running ? "running" : undefined}>
            <TraceTitle className={parked ? "text-foreground" : undefined}>{title}</TraceTitle>
            {failed > 0 && !running && (
              <TraceDescription className="text-destructive">{failed} retried</TraceDescription>
            )}
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <Trace>
            {tools.map((tool) => (
              <ToolLineA key={tool.id} tool={tool} />
            ))}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}

function ToolLineA({ tool }: { tool: Tool }) {
  const Icon = toolIcon(tool.name)
  const status = tool.running
    ? "running"
    : tool.error
      ? "error"
      : tool.parked
        ? "pending"
        : undefined
  const line = (
    <TraceLine status={status}>
      <TraceIcon>
        <Icon />
      </TraceIcon>
      <TraceTitle className="font-mono text-xs">
        {tool.name} {tool.arg}
      </TraceTitle>
      <TraceDescription className="text-xs">
        {tool.error
          ? tool.error
          : tool.parked
            ? "waiting for you"
            : [tool.gate, tool.ms !== undefined && seconds(tool.ms)].filter(Boolean).join(" · ")}
      </TraceDescription>
    </TraceLine>
  )
  if (tool.output === undefined && tool.input === undefined) return line
  return (
    <TraceCollapsible>
      <TraceCollapsibleTrigger>{line}</TraceCollapsibleTrigger>
      <TraceCollapsibleContent className="flex flex-col gap-1 font-mono text-xs">
        {tool.input && <span>{tool.input}</span>}
        {tool.output && <span className="text-foreground">{tool.output}</span>}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

function ApprovalA({ approval }: { approval: NonNullable<Turn["approval"]> }) {
  return (
    <Card size="sm" className="ring-ember/60">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldAlertIcon className="size-4 text-ember" />
          Send this email?
        </CardTitle>
        <CardDescription>{approval.rule}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <pre className="font-mono text-xs whitespace-pre-wrap text-muted-foreground">
          {approval.input}
        </pre>
        <p className="font-reading text-sm leading-6">{approval.preview}</p>
      </CardContent>
      <CardFooter className="gap-2">
        <Button size="sm">Approve</Button>
        <Button size="sm" variant="outline">
          Deny
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="sm" variant="ghost" className="ml-auto" />}>
            More
            <ChevronDownIcon data-icon="inline-end" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuGroup>
              <DropdownMenuItem>Always allow in this routine</DropdownMenuItem>
              <DropdownMenuItem>Mark email.send read-only</DropdownMenuItem>
              <DropdownMenuItem>Deny with a reason…</DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem variant="destructive">Stop the turn</DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </CardFooter>
    </Card>
  )
}

function TakeoverA({
  instance,
  takeover,
}: {
  instance: MockInstance
  takeover: NonNullable<Turn["takeover"]>
}) {
  return (
    <Card size="sm" className="ring-ember/60">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MousePointerClickIcon className="size-4 text-ember" />
          {instance.name} needs you in the browser
        </CardTitle>
        <CardDescription>{takeover.reason}</CardDescription>
      </CardHeader>
      <CardFooter className="gap-2">
        <Button size="sm">
          <GlobeIcon data-icon="inline-start" />
          Open {takeover.site}
        </Button>
        <Button size="sm" variant="ghost">
          Stop the turn
        </Button>
      </CardFooter>
    </Card>
  )
}

/** How a turn ended: a quiet row of actions on hover, or a failure that stays. */
function EndA({ turn }: { turn: Turn }) {
  const end = turn.end
  if (end === undefined) return null
  if (end.kind === "failed")
    return (
      <Alert variant="destructive">
        <TriangleAlertIcon />
        <AlertTitle>{end.message}</AlertTitle>
        <AlertDescription>
          The turn stopped ({end.code}). Nothing after the search ran.
        </AlertDescription>
        <AlertAction>
          <Button size="xs" variant="outline">
            <RefreshCwIcon data-icon="inline-start" />
            Retry
          </Button>
        </AlertAction>
      </Alert>
    )
  if (end.kind === "stopped")
    return (
      <Marker>
        <MarkerContent>Stopped</MarkerContent>
      </Marker>
    )
  return (
    <div className="-mt-2 flex items-center gap-0.5 text-xs text-muted-foreground opacity-0 transition-opacity group-last/turn:opacity-100 group-hover/turn:opacity-100 has-focus-visible:opacity-100">
      <Act label="Copy" icon={CopyIcon} />
      <Act label="Good turn" icon={ThumbsUpIcon} />
      <Act label="Bad turn" icon={ThumbsDownIcon} />
      <span className="ml-2 tabular-nums">
        {end.steps} steps · {end.tokens} tokens · {end.took}
      </span>
      {turn.recapped && (
        <Badge variant="outline" className="ml-2">
          Recapped
        </Badge>
      )}
    </div>
  )
}

function Act({ label, icon: Icon }: { label: string; icon: typeof CopyIcon }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<Button size="icon-xs" variant="ghost" aria-label={label} />}>
        <Icon />
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function Files({ files, remove }: { files: File[]; remove?: boolean }) {
  return (
    <AttachmentGroup className="justify-end">
      {files.map((f) => (
        <Attachment key={f.name} size="sm">
          <AttachmentMedia>{f.image ? <FileImageIcon /> : <FileTextIcon />}</AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle>{f.name}</AttachmentTitle>
            <AttachmentDescription>{f.meta}</AttachmentDescription>
          </AttachmentContent>
          {remove && (
            <AttachmentActions>
              <AttachmentAction aria-label="Remove">
                <XIcon />
              </AttachmentAction>
            </AttachmentActions>
          )}
        </Attachment>
      ))}
    </AttachmentGroup>
  )
}

/**
 * The composer: one soft box. Files sit above the text; + attaches, the mode and (on the home
 * screen) the instance pick sit at the foot, Send or Stop at the right.
 */
export function ComposerA({
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
  const [files, setFiles] = useState<File[]>([])
  const instance = instanceById(to)
  return (
    <InputGroup className="h-auto bg-card shadow-sm">
      {files.length > 0 && (
        <InputGroupAddon align="block-start">
          <Files files={files} remove />
        </InputGroupAddon>
      )}
      <InputGroupTextarea
        placeholder={working ? `Reply to ${instance.name}…` : `Message ${instance.name}…`}
        className="min-h-14"
      />
      <InputGroupAddon align="block-end">
        <DropdownMenu>
          <DropdownMenuTrigger render={<InputGroupButton size="icon-xs" aria-label="Add" />}>
            <PlusIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuGroup>
              <DropdownMenuItem
                onClick={() => setFiles([{ name: "lease-2026.pdf", meta: "PDF · 1.1 MB" }])}
              >
                <PaperclipIcon />
                Add files
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        {pick && (
          <DropdownMenu>
            <DropdownMenuTrigger render={<InputGroupButton size="xs" />}>
              <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
              {instance.name}
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
        )}
        <DropdownMenu>
          <DropdownMenuTrigger render={<InputGroupButton size="xs" className="ml-auto" />}>
            {MODES.find((m) => m.value === mode)?.label}
            <ChevronDownIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
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
        {working ? (
          <InputGroupButton variant="secondary" size="icon-xs" aria-label="Stop">
            <SquareIcon className="fill-current" />
          </InputGroupButton>
        ) : (
          <InputGroupButton variant="default" size="icon-xs" aria-label="Send">
            <ArrowUpIcon />
          </InputGroupButton>
        )}
      </InputGroupAddon>
    </InputGroup>
  )
}
