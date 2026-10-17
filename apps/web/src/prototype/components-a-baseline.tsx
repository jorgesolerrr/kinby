// PROTOTYPE variant A: today's compositions, rebuilt over fixtures so the others have a baseline.
import {
  ArrowUpIcon,
  BrainIcon,
  CheckIcon,
  CircleStopIcon,
  MessagesSquareIcon,
  RepeatIcon,
  ShieldAlertIcon,
  SquareIcon,
  UserIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react"

import { Tile } from "@/components/tile"
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Message, MessageContent, MessageHeader } from "@/components/ui/message"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
} from "@/components/ui/sidebar"
import { Spinner } from "@/components/ui/spinner"

import { approval, instances, tiles, turns, type ProtoTurn, type Step } from "./fixtures"
import { initial, Sheet, Specimen, STATE } from "./sheet"

export const name = "Baseline (today)"

export function ComponentsA() {
  return (
    <Sheet
      title="Baseline"
      intent="The web app as it ships today: markers above each turn, the request in a bubble, the reply flush, tool calls as one-line markers, cards for approvals and figures."
    >
      <Specimen name="Turn" note="Routine run, then a turn you started">
        <div className="flex flex-col gap-8 rounded-xl border bg-background py-6">
          {turns.map((turn) => (
            <Turn key={turn.id} turn={turn} />
          ))}
        </div>
      </Specimen>
      <Specimen name="Approval">
        <Card size="sm" className="max-w-3xl">
          <CardHeader>
            <CardTitle>{approval.call}</CardTitle>
            <CardDescription>Waiting for your answer</CardDescription>
            <CardAction>
              <Badge variant="outline">{approval.rule}</Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-3">
              <pre className="max-h-40 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap">
                {JSON.stringify(approval.arguments, null, 2)}
              </pre>
              <Field>
                <FieldLabel htmlFor="a-reason">Reason</FieldLabel>
                <Input id="a-reason" placeholder="Optional: why not, or what to do instead" />
              </Field>
            </div>
          </CardContent>
          <CardFooter>
            <div className="flex w-full flex-wrap items-center gap-2">
              <Button>
                <CheckIcon data-icon="inline-start" />
                Approve
              </Button>
              <Button variant="outline">
                <XIcon data-icon="inline-start" />
                Deny
              </Button>
              <Button variant="ghost" className="ml-auto">
                <CircleStopIcon data-icon="inline-start" />
                Stop the turn
              </Button>
            </div>
          </CardFooter>
        </Card>
      </Specimen>
      <Specimen name="Composer" note="Idle, then while a turn runs">
        <div className="flex max-w-3xl flex-col gap-4">
          <Composer running={false} />
          <Composer running />
        </div>
      </Specimen>
      <Specimen name="Instance rows" note="Sidebar">
        <SidebarProvider className="min-h-0 w-72">
          <div className="w-full rounded-xl border bg-sidebar p-2 text-sidebar-foreground">
            <SidebarMenu>
              {instances.map((instance, index) => (
                <SidebarMenuItem key={instance.id}>
                  <SidebarMenuButton
                    size="lg"
                    isActive={index === 0}
                    render={
                      <a href="#a">
                        <Avatar shape={instance.avatar.shape} aria-hidden>
                          <AvatarFallback variant={instance.avatar.color}>
                            {initial(instance.name)}
                          </AvatarFallback>
                        </Avatar>
                        <span>{instance.name}</span>
                      </a>
                    }
                  />
                  <SidebarMenuBadge>
                    {instance.needsYou > 0 ? (
                      <Badge variant="attention">{instance.needsYou}</Badge>
                    ) : instance.state === "running" ? null : (
                      <span className="text-xs text-muted-foreground">
                        {STATE[instance.state].label}
                      </span>
                    )}
                  </SidebarMenuBadge>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </div>
        </SidebarProvider>
      </Specimen>
      <Specimen name="Stat tiles">
        <div className="grid gap-4 sm:grid-cols-3">
          {tiles.map((tile) => (
            <Tile key={tile.title} {...tile} />
          ))}
        </div>
      </Specimen>
      <Specimen name="Empty state">
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <MessagesSquareIcon />
            </EmptyMedia>
            <EmptyTitle>No threads yet</EmptyTitle>
            <EmptyDescription>Start one from the composer, and it shows up here.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </Specimen>
    </Sheet>
  )
}

function Turn({ turn }: { turn: ProtoTurn }) {
  const routine = turn.startedBy.kind === "routine"
  return (
    <div className="flex flex-col gap-3 px-6">
      <Marker variant="border">
        <MarkerIcon>{routine ? <RepeatIcon /> : <UserIcon />}</MarkerIcon>
        <MarkerContent>
          {turn.startedBy.kind === "routine" ? `Routine ${turn.startedBy.name}` : "You"} · {turn.at}
        </MarkerContent>
      </Marker>
      <Message align="end">
        <MessageContent>
          <Bubble variant="secondary" align="end">
            <BubbleContent>{turn.request}</BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
      <Message>
        <MessageContent>
          <MessageHeader className="px-0">Atlas</MessageHeader>
          {turn.steps.map((step, index) => (
            <StepView key={index} step={step} />
          ))}
          <Marker>
            <MarkerIcon>{turn.end ? <CheckIcon /> : <Spinner />}</MarkerIcon>
            <MarkerContent>
              {turn.end?.kind === "done"
                ? `Done · 2 steps · ${turn.end.tokens.toLocaleString("en")} tokens`
                : "Working"}
            </MarkerContent>
          </Marker>
          {turn.recap === "episode" && (
            <Marker>
              <MarkerIcon>
                <BrainIcon />
              </MarkerIcon>
              <MarkerContent>
                <a href="#a">Recapped</a>
              </MarkerContent>
            </Marker>
          )}
        </MessageContent>
      </Message>
    </div>
  )
}

function StepView({ step }: { step: Step }) {
  if (step.kind === "text") {
    return (
      <Bubble variant="ghost">
        <BubbleContent>{step.text}</BubbleContent>
      </Bubble>
    )
  }
  const decision =
    step.status === "waiting"
      ? "waiting for you"
      : step.status === "approved"
        ? `approved by you · ${step.ms} ms`
        : `${step.ms} ms`
  return (
    <Marker>
      <MarkerIcon>{step.status === "waiting" ? <ShieldAlertIcon /> : <WrenchIcon />}</MarkerIcon>
      <MarkerContent>{`${step.name} ${step.target} · ${decision}`}</MarkerContent>
    </Marker>
  )
}

function Composer({ running }: { running: boolean }) {
  return (
    <InputGroup>
      <InputGroupTextarea aria-label="Message Atlas" placeholder="Message Atlas" />
      <InputGroupAddon align="block-end">
        <InputGroupButton
          size="icon-sm"
          variant="default"
          className="ml-auto"
          aria-label={running ? "Stop" : "Send"}
        >
          {running ? <SquareIcon className="fill-current" /> : <ArrowUpIcon />}
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  )
}
