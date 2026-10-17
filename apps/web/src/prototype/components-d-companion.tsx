// PROTOTYPE variant D, "Companion": the teammate is a presence. Avatars on both sides, the agent
// speaks in tinted cards, tool calls become small chips under what it said, and an approval is a
// plain question in its voice with a preview of the real thing (an email looks like an email).
import {
  ArrowUpIcon,
  CheckIcon,
  CircleStopIcon,
  MailIcon,
  PlusIcon,
  ShieldAlertIcon,
  SparklesIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react"

import { Avatar, AvatarBadge, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Spinner } from "@/components/ui/spinner"

import { approval, instances, tiles, turns, type ProtoTurn } from "./fixtures"
import { initial, Sheet, Specimen, STATE } from "./sheet"

export const name = "Companion"

const atlas = instances[0]

export function ComponentsD() {
  return (
    <Sheet
      title="Companion"
      intent="The teammate as a presence. Avatars on both sides of the conversation, the agent's words in soft cards, tool calls as chips under them, approvals asked as a question with a preview of the real object."
    >
      <Specimen name="Turn" note="Routine run, then a turn you started">
        <div className="flex max-w-3xl flex-col gap-8">
          {turns.map((turn) => (
            <Turn key={turn.id} turn={turn} />
          ))}
        </div>
      </Specimen>
      <Specimen name="Approval" note="Asked in the agent's voice">
        <section aria-labelledby="d-approval" className="flex max-w-3xl gap-3">
          <AtlasAvatar />
          <Card className="flex-1">
            <CardContent className="flex flex-col gap-4">
              <div className="flex flex-col gap-1">
                <h3 id="d-approval" className="text-lg font-semibold">
                  Can I send this email to Ana?
                </h3>
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <ShieldAlertIcon className="size-3.5" />
                  You asked to approve outbound email
                </p>
              </div>
              <div className="flex flex-col gap-2 rounded-lg bg-muted p-4 text-sm">
                <p className="flex items-center gap-2 text-muted-foreground">
                  <MailIcon className="size-4" /> To {approval.arguments.to}
                </p>
                <p className="font-medium">{approval.arguments.subject}</p>
                <p className="leading-relaxed">{approval.arguments.body}</p>
              </div>
              <Field>
                <FieldLabel htmlFor="d-reason">Anything to change?</FieldLabel>
                <Input id="d-reason" placeholder="Say no and tell Atlas what to do instead" />
              </Field>
              <div className="flex flex-wrap items-center gap-2">
                <Button size="lg">
                  <CheckIcon data-icon="inline-start" />
                  Yes, send it
                </Button>
                <Button size="lg" variant="outline">
                  <XIcon data-icon="inline-start" />
                  No
                </Button>
                <Button variant="link" className="ml-auto">
                  <CircleStopIcon data-icon="inline-start" />
                  Stop the turn
                </Button>
              </div>
            </CardContent>
          </Card>
        </section>
      </Specimen>
      <Specimen name="Composer" note="With suggestions, then while a turn runs">
        <div className="flex max-w-3xl flex-col gap-6">
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {["Plan my week", "Summarize yesterday's email from Ana", "What's due Friday?"].map(
                (text) => (
                  <Button key={text} variant="secondary" size="sm" className="rounded-full">
                    {text}
                  </Button>
                ),
              )}
            </div>
            <Composer running={false} />
          </div>
          <Composer running />
        </div>
      </Specimen>
      <Specimen name="Instance cards" note="Presence first">
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {instances.map((instance) => {
            const state = STATE[instance.state]
            return (
              <li key={instance.id}>
                <a
                  href="#d"
                  className="flex h-full flex-col items-start gap-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10 outline-none hover:ring-kin focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <Avatar shape={instance.avatar.shape} size="lg" aria-hidden>
                    <AvatarFallback variant={instance.avatar.color}>
                      {initial(instance.name)}
                    </AvatarFallback>
                    {instance.state === "running" && <AvatarBadge className="bg-success" />}
                  </Avatar>
                  <span className="flex flex-col">
                    <span className="font-heading text-lg font-semibold">{instance.name}</span>
                    <span className="text-sm text-muted-foreground">{instance.role}</span>
                  </span>
                  {instance.needsYou > 0 ? (
                    <Badge variant="attention">Needs you · {instance.needsYou}</Badge>
                  ) : (
                    <Badge variant={instance.state === "running" ? "success" : "secondary"}>
                      <state.icon />
                      {state.label}
                    </Badge>
                  )}
                </a>
              </li>
            )
          })}
          <li>
            <a
              href="#d"
              className="flex h-full min-h-40 flex-col items-center justify-center gap-2 rounded-xl border border-dashed text-muted-foreground outline-none hover:border-kin hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <PlusIcon className="size-5" />
              New teammate
            </a>
          </li>
        </ul>
      </Specimen>
      <Specimen name="Stat cards" note="You vs. the agent on its own">
        <div className="grid gap-4 sm:grid-cols-3">
          {tiles.map((tile, index) => (
            <Card key={tile.title} size="sm">
              <CardContent className="flex flex-col gap-2">
                <p className="text-sm text-muted-foreground">{tile.title}</p>
                <p className="font-heading text-3xl font-semibold tabular-nums">{tile.value}</p>
                {index === 0 && (
                  <div aria-hidden className="flex h-2 overflow-hidden rounded-full">
                    <span className="w-4/5 bg-chart-1" />
                    <span className="w-1/5 bg-chart-2" />
                  </div>
                )}
                <p className="text-xs text-muted-foreground">{tile.note}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </Specimen>
      <Specimen name="Empty state" note="The first hello">
        <div className="flex flex-col items-center gap-4 py-10 text-center">
          <AtlasAvatar size="lg" />
          <p className="font-heading text-4xl font-semibold">Good morning, Jorge</p>
          <p className="max-w-md text-muted-foreground">
            I'm Atlas. Tell me what's on your plate, and I'll keep track of it.
          </p>
          <div className="w-full max-w-xl">
            <Composer running={false} />
          </div>
        </div>
      </Specimen>
    </Sheet>
  )
}

function AtlasAvatar({ size }: { size?: "lg" }) {
  return (
    <Avatar shape={atlas.avatar.shape} size={size} aria-hidden>
      <AvatarFallback variant={atlas.avatar.color}>{initial(atlas.name)}</AvatarFallback>
    </Avatar>
  )
}

function Turn({ turn }: { turn: ProtoTurn }) {
  const routine = turn.startedBy.kind === "routine"
  const tools = turn.steps.filter((step) => step.kind === "tool")
  return (
    <div className="flex flex-col gap-4">
      {routine ? (
        <p className="flex w-fit items-center gap-2 rounded-full bg-ember px-3 py-1 text-xs font-medium text-on-ember">
          <SparklesIcon className="size-3.5" />
          Atlas started this · {turn.startedBy.kind === "routine" && turn.startedBy.name} ·{" "}
          {turn.at}
        </p>
      ) : (
        <div className="flex items-end justify-end gap-3">
          <Bubble variant="default" align="end">
            <BubbleContent>{turn.request}</BubbleContent>
          </Bubble>
          <Avatar size="sm" aria-hidden>
            <AvatarFallback>J</AvatarFallback>
          </Avatar>
        </div>
      )}
      <div className="flex gap-3">
        <AtlasAvatar />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <p className="text-xs text-muted-foreground">Atlas{routine ? "" : ` · ${turn.at}`}</p>
          {turn.steps.map((step, index) =>
            step.kind === "text" ? (
              <Bubble key={index} variant="tinted">
                <BubbleContent className="text-base leading-relaxed">{step.text}</BubbleContent>
              </Bubble>
            ) : null,
          )}
          <ul className="flex flex-wrap gap-1.5" aria-label="What Atlas used">
            {tools.map((step) => (
              <li key={step.name}>
                <Badge variant={step.status === "waiting" ? "default" : "outline"}>
                  {step.status === "waiting" ? <ShieldAlertIcon /> : <WrenchIcon />}
                  {step.name}
                  {step.status === "waiting"
                    ? " · needs you"
                    : step.status === "approved"
                      ? " · you approved"
                      : ""}
                </Badge>
              </li>
            ))}
            {turn.end === undefined && (
              <li>
                <Badge variant="secondary">
                  <Spinner />
                  Working
                </Badge>
              </li>
            )}
          </ul>
        </div>
      </div>
    </div>
  )
}

function Composer({ running }: { running: boolean }) {
  return (
    <InputGroup className="h-12 rounded-full bg-card pl-2">
      <InputGroupInput
        aria-label="Message Atlas"
        placeholder={running ? "Atlas is on it…" : "Message Atlas"}
        className="text-base"
      />
      <InputGroupAddon align="inline-end">
        <InputGroupButton
          size="icon-sm"
          variant={running ? "outline" : "default"}
          className="rounded-full"
          aria-label={running ? "Stop" : "Send"}
        >
          {running ? <CircleStopIcon /> : <ArrowUpIcon />}
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  )
}
