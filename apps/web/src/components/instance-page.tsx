import type {
  Client,
  Clock,
  InstanceClient,
  InstanceStatusResult,
  InstanceSummary,
  LoginSetup,
  RepositoryCommandList,
  ViewSummary,
} from "@kinby/contract"
import { useEffect, useId, useState } from "react"

import { BrowserProfiles } from "@/components/browser-profiles"
import { Secrets } from "@/components/secrets-section"
import { SubscriptionLogins } from "@/components/subscription-logins"
import { Toolset } from "@/components/toolset"
import { Vault } from "@/components/vault"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Item, ItemActions, ItemContent, ItemGroup, ItemTitle } from "@/components/ui/item"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { instanceName, observedState } from "@/lib/instances"
import { reason } from "@/lib/operation"
import { openConfig, openMemory, openStats, openView } from "@/lib/selection"
import {
  BotIcon,
  BrainIcon,
  ChartColumnIcon,
  CirclePauseIcon,
  LayoutDashboardIcon,
  SlidersHorizontalIcon,
} from "lucide-react"

/**
 * An instance's Overview, under its header, which holds its name and lifecycle. A stopped instance
 * with setup pending opens on its setup card, and keeps it until it runs. Any other stopped one
 * says it is stopped. A running one opens its config panel, its memory, its stats, and through
 * `client`, its own connection, its views, and lists its logins, asking to sign in to those that
 * are not signed in, or to sign in again once all are. Running or stopped, it shows its toolset,
 * and one the hub opens browsers for lists its browser profiles and its vault. Outside the setup
 * card, an instance whose factory role needs repository commands shows them.
 * `onChanged` hears a sign-in or a secret end, so the instances are listed again. While a stop
 * asked from the header drains, `stopping` keeps the running view.
 */
export function InstancePage({
  caller,
  client,
  clock,
  instance,
  stopping = false,
  onChanged,
}: {
  caller: Pick<Client, "call" | "subscribe">
  client?: Pick<InstanceClient, "call">
  clock: Clock
  instance: InstanceSummary
  stopping?: boolean
  onChanged: () => void
}) {
  // Read once: finishing the last sign-in on the card leaves the card open until the instance starts.
  const [openedOnSetup] = useState(instance.intended_state === "stopped" && instance.setup_pending)
  const { status, waiting } = useStatus(caller, instance)
  const name = instanceName(instance)
  const intended = stopping ? "running" : instance.intended_state
  const onSetup = openedOnSetup && intended === "stopped"

  if (onSetup) {
    if (status === undefined) return null
    return (
      <SetupCard caller={caller} clock={clock} name={name} status={status} onChanged={onChanged} />
    )
  }
  const commands = status !== undefined && hasCommands(status) && (
    <RepositoryCommands name={name} status={status} />
  )
  const toolset = (
    <Toolset caller={caller} client={client} clock={clock} instanceId={instance.instance_id} />
  )
  const profiles = status?.browser === true && (
    <>
      <BrowserProfiles caller={caller} clock={clock} instanceId={instance.instance_id} />
      <Vault caller={caller} clock={clock} instanceId={instance.instance_id} />
    </>
  )
  if (intended === "stopped") {
    return (
      <>
        <Stopped instance={instance} name={name} />
        {toolset}
        {profiles}
        {commands}
      </>
    )
  }
  // The empty state waits until the status is read. A read that failed has nothing to sign in to.
  if (intended === "running") {
    if (waiting && status === undefined) return null
    const logins = status?.setup.logins ?? []
    return (
      <>
        <div className="flex flex-wrap items-center gap-2 px-6 pt-6">
          <Button variant="outline" onClick={() => openConfig(instance.instance_id)}>
            <SlidersHorizontalIcon data-icon="inline-start" />
            Configure
          </Button>
          <Button variant="outline" onClick={() => openMemory(instance.instance_id)}>
            <BrainIcon data-icon="inline-start" />
            Memory
          </Button>
          <Button variant="outline" onClick={() => openStats(instance.instance_id)}>
            <ChartColumnIcon data-icon="inline-start" />
            Stats
          </Button>
          {client !== undefined && <ViewsMenu client={client} instanceId={instance.instance_id} />}
        </div>
        {logins.length > 0 && (
          <section className="flex max-w-2xl flex-col gap-3 p-6">
            <h2 className="text-base font-semibold">Subscription logins</h2>
            <p className="text-sm text-muted-foreground">
              {loginsNote(logins)} The instance keeps running.
            </p>
            <SubscriptionLogins
              caller={caller}
              clock={clock}
              instanceId={instance.instance_id}
              logins={logins}
              onEnded={onChanged}
            />
          </section>
        )}
        {toolset}
        {profiles}
        {commands}
        {logins.length === 0 && !commands && observedState(instance) === "running" && (
          <ReadyForWork name={name} />
        )}
      </>
    )
  }
  return null
}

/** The instance's views, read each time the menu opens. Picking one opens its page. */
function ViewsMenu({
  client,
  instanceId,
}: {
  client: Pick<InstanceClient, "call">
  instanceId: string
}) {
  const [views, setViews] = useState<ViewSummary[]>()
  const [failure, setFailure] = useState<string>()
  const list = () => {
    setFailure(undefined)
    client.call("view.list", {}).then(
      (listed) => setViews(listed.views),
      (error: unknown) => setFailure(reason(error)),
    )
  }
  return (
    <DropdownMenu onOpenChange={(open) => open && list()}>
      <DropdownMenuTrigger render={<Button variant="outline" />}>
        <LayoutDashboardIcon data-icon="inline-start" />
        Views
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {failure !== undefined || views === undefined || views.length === 0 ? (
          <DropdownMenuItem disabled>
            {failure ?? (views === undefined ? "Reading…" : "No views yet")}
          </DropdownMenuItem>
        ) : (
          views.map((view) => (
            <DropdownMenuItem key={view.id} onClick={() => openView(instanceId, view.id)}>
              {view.title}
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** A running instance with nothing to set up, pointing to where its work happens. */
function ReadyForWork({ name }: { name: string }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <BotIcon />
        </EmptyMedia>
        <EmptyTitle>{name} is running</EmptyTitle>
        <EmptyDescription>
          Start a thread with New thread above {name}&apos;s threads, or open Configure to change
          what it does.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}

/** Names the logins not signed in, pending or failed, or offers signing in again once none is. */
function loginsNote(logins: LoginSetup[]): string {
  const notSignedIn = logins.filter((login) => login.state !== "signed_in")
  if (notSignedIn.length === 0) return "Sign in again when a subscription stops working."
  const names = new Intl.ListFormat("en").format(notSignedIn.map((login) => login.label))
  return notSignedIn.length === 1
    ? `${names} is not signed in yet. Sign in to let the instance use it.`
    : `${names} are not signed in yet. Sign in to let the instance use them.`
}

/**
 * The instance's status, read again each time the instance is listed again. The last one read
 * stays while the next is on its way, so the rows that follow a sign-in stay where they are.
 * `waiting` is true until the first read settles.
 */
function useStatus(
  caller: Pick<Client, "call">,
  instance: InstanceSummary,
): { status: InstanceStatusResult | undefined; waiting: boolean } {
  const [status, setStatus] = useState<InstanceStatusResult>()
  const [waiting, setWaiting] = useState(true)
  useEffect(() => {
    let current = true
    caller.call("instance.status", { instance_id: instance.instance_id }).then(
      (read) => {
        if (!current) return
        setStatus(read)
        setWaiting(false)
      },
      // A status that cannot be read keeps the one already shown.
      () => {
        if (current) setWaiting(false)
      },
    )
    return () => {
      current = false
    }
  }, [caller, instance])
  return { status, waiting }
}

function SetupCard({
  caller,
  clock,
  name,
  status,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  name: string
  status: InstanceStatusResult
  onChanged: () => void
}) {
  const titleId = useId()
  const { logins, secrets, missing_commands: missing } = status.setup
  return (
    <section aria-labelledby={titleId} className="p-6">
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle id={titleId}>Finish setting up {name}</CardTitle>
          <CardDescription>
            Sign in to what it declares, and set its secrets. It starts without them too. A secret
            you set applies when {name} starts.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-6">
            {logins.length > 0 && (
              <section className="flex flex-col gap-3">
                <h3 className="font-medium">Sign in</h3>
                <SubscriptionLogins
                  caller={caller}
                  clock={clock}
                  instanceId={status.instance_id}
                  logins={logins}
                  onEnded={onChanged}
                />
              </section>
            )}
            {secrets.length > 0 && (
              <section className="flex flex-col gap-3">
                <h3 className="font-medium">Secrets</h3>
                <Secrets
                  caller={caller}
                  clock={clock}
                  instanceId={status.instance_id}
                  secrets={secrets}
                  onSet={onChanged}
                />
              </section>
            )}
            {missing.length > 0 && (
              <section className="flex flex-col gap-3">
                <h3 className="font-medium">Repository commands</h3>
                <p className="text-sm text-muted-foreground">
                  {name} takes no work until it holds these lists. Start {name} and ask it in a
                  thread for a repository survey.
                </p>
                <CommandLists held={status.repository_commands} missing={missing} />
              </section>
            )}
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

function hasCommands(status: InstanceStatusResult): boolean {
  return status.repository_commands.length > 0 || status.setup.missing_commands.length > 0
}

/**
 * The repository commands the instance holds and those its factory role needs that it lacks.
 * They are read-only here: the instance records them.
 */
function RepositoryCommands({ name, status }: { name: string; status: InstanceStatusResult }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex max-w-2xl flex-col gap-3 p-6">
      <h2 id={titleId} className="text-base font-semibold">
        Repository commands
      </h2>
      <p className="text-sm text-muted-foreground">
        What {name} runs for the repository it works in. To change them, ask {name} in a thread.
      </p>
      <CommandLists held={status.repository_commands} missing={status.setup.missing_commands} />
    </section>
  )
}

/** Each list held, by name with its commands in the order they run, then each missing one. */
function CommandLists({ held, missing }: { held: RepositoryCommandList[]; missing: string[] }) {
  return (
    <ItemGroup aria-label="Repository commands">
      {held.map(({ name, commands }) => (
        <Item key={name} render={<li />} aria-label={name} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>
              <code className="font-mono">{name}</code>
            </ItemTitle>
            {commands.length > 0 && (
              <ol className="flex flex-col gap-1 text-muted-foreground">
                {commands.map((command, index) => (
                  // A list may run the same command twice.
                  <li key={index}>
                    <code className="font-mono break-all">{command}</code>
                  </li>
                ))}
              </ol>
            )}
          </ItemContent>
          {commands.length === 0 && (
            <ItemActions>
              <Badge variant="outline">Empty</Badge>
            </ItemActions>
          )}
        </Item>
      ))}
      {missing.map((list) => (
        <Item key={list} render={<li />} aria-label={list} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>
              <code className="font-mono">{list}</code>
            </ItemTitle>
          </ItemContent>
          <ItemActions>
            <Badge variant="outline">Missing</Badge>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

/** Says the instance is stopped, and how the last run ended when it crashed, since a stop keeps its error. */
function Stopped({ instance, name }: { instance: InstanceSummary; name: string }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-1">
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <CirclePauseIcon />
          </EmptyMedia>
          <EmptyTitle id={titleId}>{name} is stopped</EmptyTitle>
          <EmptyDescription>It does nothing until it starts.</EmptyDescription>
          {instance.process === "failed" && (
            <EmptyDescription>The last run ended with an error: {instance.detail}</EmptyDescription>
          )}
        </EmptyHeader>
      </Empty>
    </section>
  )
}
