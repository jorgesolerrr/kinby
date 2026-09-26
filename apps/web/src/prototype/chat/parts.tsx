// PROTOTYPE, throwaway. Pieces the three variants share: a tool line, approval buttons, the
// composer, the mode picker, and the floating state panel with the call log.
import type { PermissionMode } from "@kinby/contract"
import { cn } from "cn"
import type * as React from "react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  ArrowUpIcon,
  BanIcon,
  CheckIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  CircleStopIcon,
  FileIcon,
  PaperclipIcon,
  PencilIcon,
  ShieldAlertIcon,
  TerminalIcon,
  XIcon,
} from "lucide-react"

import type { Chat, ToolStep, TurnView } from "./flow"
import { INSTANCE, MODES } from "./stub"

export function mainArg(step: ToolStep): string {
  const a = step.args
  return String(a.path ?? a.command ?? a.query ?? Object.values(a)[0] ?? "")
}

export function toolIcon(step: ToolStep) {
  if (step.name === "run_command") return <TerminalIcon />
  if (step.write) return <PencilIcon />
  return <FileIcon />
}

export function GateBadge({ step }: { step: ToolStep }) {
  if (step.approval && !step.gate)
    return (
      <Badge variant="outline">
        <ShieldAlertIcon data-icon="inline-start" />
        Waiting for you
      </Badge>
    )
  if (!step.gate) return <Spinner />
  if (step.gate.action === "deny")
    return (
      <Badge variant="destructive">
        <BanIcon data-icon="inline-start" />
        Denied by {step.gate.decidedBy === "user" ? "you" : "policy"}
      </Badge>
    )
  if (step.gate.decidedBy === "user")
    return (
      <Badge variant="secondary">
        <CheckIcon data-icon="inline-start" />
        Approved by you
      </Badge>
    )
  if (!step.result) return <Spinner />
  return step.result.error ? (
    <Badge variant="destructive">error</Badge>
  ) : (
    <Badge variant="ghost">{step.result.durationMs ?? 0} ms</Badge>
  )
}

/** One tool call as a single collapsible line: name, main argument, gate, result on open. */
export function ToolLine({ step }: { step: ToolStep }) {
  return (
    <Collapsible>
      <CollapsibleTrigger
        render={
          <Button variant="ghost" size="sm" className="w-full justify-start font-normal" />
        }
      >
        <ChevronRightIcon data-icon="inline-start" />
        {toolIcon(step)}
        <span className="font-mono">{step.name}</span>
        <span className="min-w-0 truncate text-muted-foreground">{mainArg(step)}</span>
        <span className="ml-auto">
          <GateBadge step={step} />
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="ml-8 max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
          {JSON.stringify(step.args, null, 2)}
          {step.gate && `\n\n${step.gate.action} · ${step.gate.rule}`}
          {step.result && `\n\n${step.result.output}`}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function ApprovalActions({ chat, withNote }: { chat: Chat; withNote?: boolean }) {
  const [note, setNote] = useState("")
  const [noting, setNoting] = useState(false)
  if (noting)
    return (
      <InputGroup>
        <InputGroupInput
          autoFocus
          placeholder="Tell it why, or what to do instead"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <InputGroupAddon align="inline-end">
          <InputGroupButton onClick={() => chat.respond("no", note)}>Deny</InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
    )
  return (
    <div className="flex flex-wrap gap-2">
      <Button size="sm" onClick={() => chat.respond("yes")}>
        <CheckIcon data-icon="inline-start" />
        Approve
      </Button>
      <Button size="sm" variant="outline" onClick={() => chat.respond("no")}>
        <XIcon data-icon="inline-start" />
        Deny
      </Button>
      {withNote && (
        <Button size="sm" variant="ghost" onClick={() => setNoting(true)}>
          Deny with a note
        </Button>
      )}
    </div>
  )
}

export function ModeSelect({ chat }: { chat: Chat }) {
  return (
    <Select value={chat.mode} onValueChange={(v) => chat.setMode(v as PermissionMode)}>
      <SelectTrigger size="sm">
        <SelectValue>{(v: string) => MODES.find((m) => m.mode === v)?.label}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {MODES.map((m) => (
            <SelectItem key={m.mode} value={m.mode} disabled={!INSTANCE.allowedModes.includes(m.mode)}>
              {m.label}
              <span className="text-muted-foreground">{m.hint}</span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

export function Composer({
  chat,
  modeSlot,
  placeholder,
}: {
  chat: Chat
  modeSlot?: React.ReactNode
  placeholder?: string
}) {
  const [text, setText] = useState("")
  const [files, setFiles] = useState<string[]>([])
  const running = chat.view.running
  const send = () => {
    if (!text.trim() || running) return
    chat.startTurn(text.trim(), files)
    setText("")
    setFiles([])
  }
  return (
    <InputGroup>
      <InputGroupTextarea
        placeholder={
          placeholder ??
          (running ? `${INSTANCE.name} is working. Stop it to send something else.` : `Message ${INSTANCE.name}`)
        }
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            send()
          }
        }}
      />
      <InputGroupAddon align="block-end">
        <InputGroupButton
          size="icon-xs"
          aria-label="Attach a file"
          onClick={() => setFiles((f) => [...f, `screenshot-${f.length + 1}.png`])}
        >
          <PaperclipIcon />
        </InputGroupButton>
        {files.map((f) => (
          <Badge key={f} variant="secondary">
            {f}
          </Badge>
        ))}
        {modeSlot}
        {running ? (
          <InputGroupButton
            className="ml-auto"
            size="icon-xs"
            variant="default"
            aria-label="Stop"
            onClick={chat.interrupt}
          >
            <CircleStopIcon />
          </InputGroupButton>
        ) : (
          <InputGroupButton
            className="ml-auto"
            size="icon-xs"
            variant="default"
            aria-label="Send"
            disabled={!text.trim()}
            onClick={send}
          >
            <ArrowUpIcon />
          </InputGroupButton>
        )}
      </InputGroupAddon>
    </InputGroup>
  )
}

export function TurnEnd({ turn }: { turn: TurnView }) {
  const end = turn.end
  if (!end)
    return (
      <Marker>
        <MarkerIcon>
          <Spinner />
        </MarkerIcon>
        <MarkerContent className="shimmer">Working</MarkerContent>
      </Marker>
    )
  if (end.kind === "failed")
    return (
      <Marker>
        <MarkerIcon>
          <CircleAlertIcon />
        </MarkerIcon>
        <MarkerContent>
          Failed: {end.message} ({end.code})
        </MarkerContent>
      </Marker>
    )
  if (end.kind === "interrupted")
    return (
      <Marker>
        <MarkerIcon>
          <CircleStopIcon />
        </MarkerIcon>
        <MarkerContent>You stopped this turn</MarkerContent>
      </Marker>
    )
  return null
}

export function TranscriptSkeleton() {
  return (
    <div className="flex flex-col gap-6 p-6">
      <Skeleton className="ml-auto h-10 w-2/5" />
      <Skeleton className="h-24 w-3/5" />
      <Skeleton className="ml-auto h-10 w-1/3" />
      <Skeleton className="h-16 w-1/2" />
    </div>
  )
}

export function StatePanel({ chat }: { chat: Chat }) {
  const [open, setOpen] = useState(true)
  const { sub, view } = chat
  return (
    <div className="fixed top-3 right-3 z-50 w-80 rounded-xl border-2 border-dashed border-foreground/40 bg-background/95 p-3 text-xs shadow-lg">
      <div className="flex items-center justify-between">
        <span className="font-semibold">Prototype state</span>
        <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      </div>
      {open && (
        <div className="mt-2 flex flex-col gap-2">
          <div className="grid grid-cols-2 gap-x-2 font-mono">
            <span>head_sequence</span>
            <span>{sub.headSequence ?? "…"}</span>
            <span>stream</span>
            <span>{sub.replayed ? "live" : "replaying"}</span>
            <span>mode</span>
            <span>
              {chat.mode}
              {view.pinnedMode ? " (pinned)" : " (default, guessed)"}
            </span>
            <span>turn</span>
            <span>{view.pending ? "parked" : view.running ? "running" : "idle"}</span>
            <span>events</span>
            <span>{chat.thread.events.length}</span>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="slow"
              checked={chat.slowReplay}
              onCheckedChange={(v) => chat.setSlowReplay(v === true)}
            />
            <Label htmlFor="slow">Slow replay (2.5 s) to see the loading transcript</Label>
          </div>
          <p className="text-muted-foreground">
            Ask mode parks the write for approval. Read-only denies it. Auto runs it.
          </p>
          <div className="max-h-72 overflow-auto rounded-md bg-muted p-2 font-mono">
            {chat.log.length === 0 && <span className="text-muted-foreground">No calls yet.</span>}
            {chat.log.map((entry, i) => (
              <details key={i} className="py-0.5">
                <summary className={cn(entry.missing && "text-destructive")}>
                  {entry.method}
                  {entry.missing && " · not in contract"}
                </summary>
                <pre className="whitespace-pre-wrap">
                  {entry.missing && `${entry.missing}\n`}
                  {JSON.stringify(entry.params, null, 2)}
                </pre>
              </details>
            ))}
          </div>
          <Button size="xs" variant="outline" onClick={chat.reset}>
            Reset
          </Button>
        </div>
      )}
    </div>
  )
}
