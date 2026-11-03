// PROTOTYPE · The empty states, two variants each.
// First run (a hub holding only the home instance, its API key pending):
//   A "Setup in place": the new-thread screen, with the key asked for in a card over the composer.
//   B "Get started": a short list of first steps, the key one unfolding in place.
// An instance with no threads: A "Greeting" (avatar, one line, the composer) or B "Starters"
//   (the composer with a row per picked skill that fills it).
// A stopped instance: A keeps the composer and turns Send into Start and send; B swaps the
//   composer for an Empty with Start.
// An empty Library tab: A an Empty block with the tab's action; B the tab's list frame with one
//   placeholder row and an Add row.
import {
  ArrowUpIcon,
  CheckIcon,
  ChevronDownIcon,
  CirclePauseIcon,
  KeyRoundIcon,
  MessageSquareIcon,
  PlayIcon,
  PlugIcon,
  PlusIcon,
  SparklesIcon,
} from "lucide-react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "cn"

import type { Kind } from "../library/mock"
import type { MockInstance } from "../shell/mock"
import type { Go } from "../shell/screens"
import { ATLAS_SKILLS, OFFERED_SKILLS } from "./mock"
import { type Draft, Rows, SetupRows } from "./shared"
import { fieldsFor, ROLES } from "./mock"

// ── A composer stand-in (its design belongs to the thread ticket) ──────────────────────────────

function Composer({
  instance,
  disabled,
  placeholder,
  value,
  onValue,
  send,
}: {
  instance: MockInstance
  disabled?: boolean
  placeholder?: string
  value?: string
  onValue?: (v: string) => void
  send?: React.ReactNode
}) {
  return (
    <InputGroup className={cn("h-auto", disabled && "opacity-60")}>
      <InputGroupTextarea
        disabled={disabled}
        placeholder={placeholder ?? `Message ${instance.name}…`}
        className="min-h-16"
        value={value}
        onChange={onValue && ((e) => onValue(e.target.value))}
      />
      <InputGroupAddon align="block-end">
        <span className="flex items-center gap-1.5 px-1 text-xs text-muted-foreground">
          <InstanceAvatar avatar={instance.avatar} name={instance.name} size="sm" />
          {instance.name}
        </span>
        {send ?? (
          <InputGroupButton
            variant="default"
            size="icon-xs"
            className="ml-auto"
            aria-label="Send"
            disabled={disabled}
          >
            <ArrowUpIcon />
          </InputGroupButton>
        )}
      </InputGroupAddon>
    </InputGroup>
  )
}

function Centre({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 overflow-y-auto px-4 pb-24">
      {children}
    </div>
  )
}

// ── First run ──────────────────────────────────────────────────────────────────────────────────

/** The home instance's built-in fields: model and API key. */
function useKey() {
  const [draft, setDraft] = useState<Draft>({
    role: ROLES[0],
    name: "Kinby",
    shape: "circle",
    color: "green",
    skills: [],
    prompt: ROLES[0].prompt,
    values: { model: "claude-sonnet-5-5" },
    linked: {},
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const filled = (draft.values.api_key ?? "").trim() !== "" || draft.linked.api_key !== undefined
  const save = () => {
    setSaving(true)
    window.setTimeout(() => {
      setSaving(false)
      setSaved(true)
    }, 900)
  }
  return { draft, setDraft, saving, saved, filled, save }
}

export function FirstRunA({ home }: { home: MockInstance }) {
  const key = useKey()
  return (
    <Centre>
      <div className="flex flex-col items-center gap-2 text-center">
        <h1 className="font-heading text-3xl font-semibold">Welcome to kinby</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          {home.name} is your home instance. It does personal work for you and builds the rest of
          kinby on your behalf.
        </p>
      </div>
      <div className="flex w-full max-w-3xl flex-col gap-3">
        {!key.saved && (
          <Card size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRoundIcon className="size-4 text-ember" />
                {home.name} needs an API key to answer
              </CardTitle>
              <CardDescription>
                Pick its model and paste the provider's key. The hub keeps the key and never shows
                it back.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <SetupRows draft={key.draft} fields={fieldsFor(ROLES[0])} onChange={key.setDraft} />
              <Button className="self-end" disabled={!key.filled || key.saving} onClick={key.save}>
                {key.saving && <Spinner data-icon="inline-start" />}
                Save and restart {home.name}
              </Button>
            </CardContent>
          </Card>
        )}
        <Composer
          instance={home}
          disabled={!key.saved}
          placeholder={key.saved ? undefined : `Add ${home.name}'s key to start`}
        />
      </div>
    </Centre>
  )
}

export function FirstRunB({ home, go }: { home: MockInstance; go: Go }) {
  const key = useKey()
  const [open, setOpen] = useState<"key" | "hello" | undefined>("key")
  const step = (n: number, done: boolean) => (
    <span
      className={cn(
        "grid size-6 shrink-0 place-items-center rounded-full border text-xs font-medium",
        done && "border-transparent bg-kin text-white",
      )}
    >
      {done ? <CheckIcon className="size-3.5" /> : n}
    </span>
  )
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 py-12 md:px-8">
        <div className="flex flex-col gap-2">
          <h1 className="font-heading text-3xl font-semibold">Welcome to kinby</h1>
          <p className="text-sm text-muted-foreground">
            Three steps and {home.name}, your home instance, is working for you.
          </p>
        </div>
        <Rows>
          <Collapsible open={open === "key"} onOpenChange={(o) => setOpen(o ? "key" : undefined)}>
            <CollapsibleTrigger
              render={
                <Item
                  className="w-full cursor-pointer text-left hover:bg-muted/50"
                  render={<button type="button" />}
                />
              }
            >
              {step(1, key.saved)}
              <ItemContent>
                <ItemTitle>Give {home.name} an API key</ItemTitle>
                <ItemDescription>
                  Its model and the provider's key. Nothing runs without it.
                </ItemDescription>
              </ItemContent>
              <ItemActions>
                <ChevronDownIcon
                  className={cn(
                    "size-4 text-muted-foreground transition-transform",
                    open === "key" && "rotate-180",
                  )}
                />
              </ItemActions>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="flex flex-col gap-3 border-t bg-muted/40 p-4">
                <SetupRows draft={key.draft} fields={fieldsFor(ROLES[0])} onChange={key.setDraft} />
                <Button
                  size="sm"
                  className="self-end"
                  disabled={!key.filled || key.saving || key.saved}
                  onClick={() => {
                    key.save()
                    window.setTimeout(() => setOpen("hello"), 950)
                  }}
                >
                  {key.saving && <Spinner data-icon="inline-start" />}
                  {key.saved ? "Saved" : "Save"}
                </Button>
              </div>
            </CollapsibleContent>
          </Collapsible>
          <Collapsible
            open={open === "hello"}
            onOpenChange={(o) => key.saved && setOpen(o ? "hello" : undefined)}
          >
            <CollapsibleTrigger
              render={
                <Item
                  className={cn(
                    "w-full text-left",
                    key.saved ? "cursor-pointer hover:bg-muted/50" : "text-muted-foreground",
                  )}
                  render={<button type="button" />}
                />
              }
            >
              {step(2, false)}
              <ItemContent>
                <ItemTitle>Say hello to {home.name}</ItemTitle>
                <ItemDescription>Tell it what you'd like a hand with.</ItemDescription>
              </ItemContent>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="border-t bg-muted/40 p-4">
                <Composer instance={home} placeholder="Hi! Can you help me plan my week?" />
              </div>
            </CollapsibleContent>
          </Collapsible>
          <Item
            className="w-full cursor-pointer text-left hover:bg-muted/50"
            render={<button type="button" onClick={() => go({ kind: "new-instance" })} />}
          >
            {step(3, false)}
            <ItemContent>
              <ItemTitle>Make an instance of your own</ItemTitle>
              <ItemDescription>
                A teammate for one area: research, trips, a repository.
              </ItemDescription>
            </ItemContent>
            <ItemActions>
              <span className="text-xs text-muted-foreground">Optional</span>
            </ItemActions>
          </Item>
        </Rows>
      </div>
    </div>
  )
}

// ── An instance with no threads ────────────────────────────────────────────────────────────────

export function NoThreadsA({ instance }: { instance: MockInstance }) {
  return (
    <Centre>
      <div className="flex flex-col items-center gap-3 text-center">
        <InstanceAvatar avatar={instance.avatar} name={instance.name} size="lg" />
        <h1 className="font-heading text-3xl font-semibold">{instance.name} is ready</h1>
        <p className="text-sm text-muted-foreground">
          No threads yet. It holds {ATLAS_SKILLS.length} skills:{" "}
          <span className="font-mono text-foreground">{ATLAS_SKILLS.join(", ")}</span>.
        </p>
      </div>
      <div className="w-full max-w-3xl">
        <Composer instance={instance} />
      </div>
    </Centre>
  )
}

const STARTERS: Record<string, string> = {
  "trip-planner": "Plan a long weekend in Porto in May, under 600 euros.",
  research: "What's the evidence on standing desks? Primary sources only.",
}

export function NoThreadsB({ instance }: { instance: MockInstance }) {
  const [text, setText] = useState("")
  return (
    <Centre>
      <h1 className="font-heading text-3xl font-semibold">What should {instance.name} do first?</h1>
      <div className="flex w-full max-w-3xl flex-col gap-4">
        <Composer instance={instance} value={text} onValue={setText} />
        <Rows>
          {ATLAS_SKILLS.map((name) => (
            <Item
              key={name}
              size="sm"
              className="w-full cursor-pointer text-left hover:bg-muted/50"
              render={<button type="button" onClick={() => setText(STARTERS[name] ?? "")} />}
            >
              <SparklesIcon className="size-4 text-muted-foreground" />
              <ItemContent className="min-w-0">
                <ItemTitle className="font-normal">{STARTERS[name]}</ItemTitle>
                <ItemDescription className="line-clamp-1">
                  <span className="font-mono">{name}</span> ·{" "}
                  {OFFERED_SKILLS.find((s) => s.name === name)?.description}
                </ItemDescription>
              </ItemContent>
            </Item>
          ))}
        </Rows>
      </div>
    </Centre>
  )
}

// ── A stopped instance ─────────────────────────────────────────────────────────────────────────

function useStart() {
  const [state, setState] = useState<"stopped" | "starting" | "running">("stopped")
  const start = () => {
    setState("starting")
    window.setTimeout(() => setState("running"), 1200)
  }
  return { state, start }
}

export function StoppedA({ instance }: { instance: MockInstance }) {
  const s = useStart()
  const [text, setText] = useState("")
  const live = { ...instance, state: s.state === "running" ? "running" : "stopped" } as MockInstance
  return (
    <Centre>
      <h1 className="font-heading text-3xl font-semibold">Good evening, Jorge</h1>
      <div className="flex w-full max-w-3xl flex-col gap-3">
        {s.state !== "running" && (
          <Alert>
            <CirclePauseIcon />
            <AlertTitle>{instance.name} is stopped</AlertTitle>
            <AlertDescription>Write anyway: sending starts it first.</AlertDescription>
            <AlertAction>
              <Button
                size="sm"
                variant="outline"
                disabled={s.state === "starting"}
                onClick={s.start}
              >
                {s.state === "starting" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <PlayIcon data-icon="inline-start" />
                )}
                Start
              </Button>
            </AlertAction>
          </Alert>
        )}
        <Composer
          instance={live}
          value={text}
          onValue={setText}
          send={
            s.state === "running" ? undefined : (
              <InputGroupButton
                variant="default"
                size="xs"
                className="ml-auto"
                disabled={text.trim() === "" || s.state === "starting"}
                onClick={s.start}
              >
                {s.state === "starting" ? <Spinner /> : <PlayIcon />}
                Start and send
              </InputGroupButton>
            )
          }
        />
      </div>
    </Centre>
  )
}

export function StoppedB({ instance, go }: { instance: MockInstance; go: Go }) {
  const s = useStart()
  if (s.state === "running") return <NoThreadsA instance={{ ...instance, state: "running" }} />
  return (
    <Centre>
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <CirclePauseIcon />
          </EmptyMedia>
          <EmptyTitle>{instance.name} is stopped</EmptyTitle>
          <EmptyDescription>
            Start it to chat. Its threads, memory and routines are kept while it's stopped.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent className="flex-row justify-center">
          <Button disabled={s.state === "starting"} onClick={s.start}>
            {s.state === "starting" ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <PlayIcon data-icon="inline-start" />
            )}
            Start {instance.name}
          </Button>
          <Button
            variant="outline"
            onClick={() => go({ kind: "instance", instanceId: instance.id, tab: "overview" })}
          >
            Open its page
          </Button>
        </EmptyContent>
      </Empty>
    </Centre>
  )
}

// ── An empty Library tab ───────────────────────────────────────────────────────────────────────

const LEADS: Record<Kind, { lead: string; add: string }> = {
  mcp_server: {
    lead: "An MCP server gives an instance tools from outside kinby, like GitHub or Notion. Add one here, then pick it for an instance.",
    add: "Add MCP server",
  },
  skill: {
    lead: "A skill teaches an instance one kind of task. Write one, or add some from a git repository.",
    add: "Write a skill",
  },
  factory: {
    lead: "A factory runs work through steps across instances. Ask an instance with the write-factory skill to make one.",
    add: "",
  },
}

export function LibraryEmptyA({ kind, add, go }: { kind: Kind; add: () => void; go: Go }) {
  const copy = LEADS[kind]
  return (
    <Empty className="border border-dashed">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          {kind === "mcp_server" ? <PlugIcon /> : <SparklesIcon />}
        </EmptyMedia>
        <EmptyTitle>
          No {kind === "mcp_server" ? "MCP servers" : kind === "skill" ? "skills" : "factories"} yet
        </EmptyTitle>
        <EmptyDescription>{copy.lead}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="flex-row justify-center">
        {copy.add !== "" && (
          <Button onClick={add}>
            <PlusIcon data-icon="inline-start" />
            {copy.add}
          </Button>
        )}
        <Button variant="outline" onClick={() => go({ kind: "home" })}>
          <MessageSquareIcon data-icon="inline-start" />
          Ask Kin to add one
        </Button>
      </EmptyContent>
    </Empty>
  )
}

export function LibraryEmptyB({ kind, add }: { kind: Kind; add: () => void }) {
  const copy = LEADS[kind]
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 className="font-heading text-base font-semibold">
          {kind === "mcp_server" ? "MCP servers" : kind === "skill" ? "Skills" : "Factories"}
        </h2>
        <p className="text-sm text-muted-foreground">
          Tools an instance reaches over MCP once it picks the server
        </p>
      </div>
      <Rows>
        <Item size="sm">
          <ItemContent>
            <ItemTitle className="text-muted-foreground">None yet</ItemTitle>
            <ItemDescription>{copy.lead}</ItemDescription>
          </ItemContent>
        </Item>
        {copy.add !== "" && (
          <Item
            size="sm"
            className="w-full cursor-pointer text-left text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            render={<button type="button" onClick={add} />}
          >
            <PlusIcon className="size-4" />
            <ItemContent>
              <ItemTitle className="font-normal">{copy.add}</ItemTitle>
            </ItemContent>
          </Item>
        )}
      </Rows>
    </section>
  )
}
