import type {
  Client,
  Clock,
  InstanceClient,
  InstanceStatusResult,
  InstanceSummary,
  McpServerFailed,
  RepositoryCommandList,
} from "@kinby/contract"
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useState,
  useSyncExternalStore,
} from "react"

import { BrowserProfiles } from "@/components/browser-profiles"
import { SectionFrame } from "@/components/section-frame"
import { Secrets } from "@/components/secrets-section"
import { LoginRow, SubscriptionLogins } from "@/components/subscription-logins"
import { Toolset } from "@/components/toolset"
import { Vault } from "@/components/vault"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { POLL_INTERVAL_MS, usePolled } from "@/hooks/use-polled"
import { instanceName, noticeKey, noticeTitle, observedState } from "@/lib/instances"
import { openSkills, openView } from "@/lib/selection"
import { BotIcon, ChevronRightIcon, CirclePauseIcon, LayoutDashboardIcon } from "lucide-react"

/**
 * An instance's Overview, under its header, which holds its name and lifecycle. A stopped instance
 * with setup pending opens on its setup card, and keeps it until it runs. Any other stopped one
 * says it is stopped. A running one opens on what needs the user: its logins not signed in, each
 * to sign in to, and its MCP servers that failed. Through `client`, its own connection, it lists
 * its views. Running or stopped, it shows its toolset, and one the hub opens browsers for lists its
 * browser profiles and its vault. Outside the setup card, an instance whose factory role needs
 * repository commands shows them. `onChanged` hears a sign-in or a secret end, so the instances
 * are listed again. While a stop asked from the header drains, `stopping` keeps the running view.
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
  client?: Pick<InstanceClient, "call" | "state" | "onStateChange">
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
  const failed = instance.notices.filter(
    (notice): notice is McpServerFailed => notice.code === "mcp_server_failed",
  )
  const sections = (
    <>
      {status?.browser === true && (
        <>
          <BrowserProfiles caller={caller} clock={clock} instanceId={instance.instance_id} />
          <Vault caller={caller} clock={clock} instanceId={instance.instance_id} />
        </>
      )}
      <Toolset
        caller={caller}
        client={client}
        clock={clock}
        instanceId={instance.instance_id}
        failed={failed.map((notice) => notice.server)}
      />
      {status !== undefined && hasCommands(status) && (
        <RepositoryCommands name={name} status={status} />
      )}
    </>
  )
  if (intended === "stopped") {
    return (
      <>
        <Stopped instance={instance} name={name} />
        <Sections>{sections}</Sections>
      </>
    )
  }
  // The empty state waits until the status is read. A read that failed has nothing to sign in to.
  if (intended === "running") {
    if (waiting && status === undefined) return null
    const logins = (status?.setup.logins ?? []).filter((login) => login.state !== "signed_in")
    const needsYou = logins.length > 0 || failed.length > 0
    return (
      <Sections>
        {needsYou && (
          <SectionFrame
            title="Needs you"
            lead={`What keeps ${name} from doing everything it could.`}
            variant="page"
          >
            <ItemGroup variant="outline" aria-label="Needs you">
              {logins.map((login) => (
                <LoginRow
                  key={login.id}
                  caller={caller}
                  clock={clock}
                  instanceId={instance.instance_id}
                  login={login}
                  onEnded={onChanged}
                  variant="default"
                  size="sm"
                />
              ))}
              {failed.map((notice) => (
                <li key={noticeKey(notice)} aria-label={noticeTitle(notice)}>
                  <Item size="sm">
                    <ItemContent>
                      <ItemTitle>{noticeTitle(notice)}</ItemTitle>
                      <ItemDescription lines="all">{notice.message}</ItemDescription>
                    </ItemContent>
                    <ItemActions>
                      <Button variant="outline" onClick={() => openSkills(instance.instance_id)}>
                        Open Skills
                      </Button>
                    </ItemActions>
                  </Item>
                </li>
              ))}
            </ItemGroup>
          </SectionFrame>
        )}
        {client !== undefined && (
          <Views client={client} clock={clock} instanceId={instance.instance_id} name={name} />
        )}
        {sections}
        {/* An instance missing a list of repository commands takes no work. */}
        {!needsYou &&
          (status?.setup.missing_commands.length ?? 0) === 0 &&
          observedState(instance) === "running" && <ReadyForWork name={name} />}
      </Sections>
    )
  }
  return null
}

/** Overview's sections, one under the other. */
function Sections({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-8 py-6">{children}</div>
}

/** The instance's views as rows, read again as the sidebar reads them. Picking one opens its page. */
function Views({
  client,
  clock,
  instanceId,
  name,
}: {
  client: Pick<InstanceClient, "call" | "state" | "onStateChange">
  clock: Clock
  instanceId: string
  name: string
}) {
  const connected = useSyncExternalStore(client.onStateChange, client.state) === "connected"
  const list = useCallback(() => client.call("view.list", {}), [client])
  const [value] = usePolled(list, clock, connected, POLL_INTERVAL_MS)
  return (
    <SectionFrame title="Views" lead={`Pages ${name} keeps current.`} variant="page">
      {value !== undefined &&
        (value.views.length === 0 ? (
          <p className="text-sm text-muted-foreground">No views yet.</p>
        ) : (
          <ItemGroup variant="outline" aria-label="Views">
            {value.views.map((view) => (
              <li key={view.id}>
                <Item
                  size="sm"
                  render={<button type="button" aria-label={view.title} />}
                  onClick={() => openView(instanceId, view.id)}
                >
                  <ItemMedia variant="icon">
                    <LayoutDashboardIcon />
                  </ItemMedia>
                  <ItemContent>
                    <ItemTitle>{view.title}</ItemTitle>
                  </ItemContent>
                  <ItemActions>
                    <ChevronRightIcon className="text-muted-foreground" />
                  </ItemActions>
                </Item>
              </li>
            ))}
          </ItemGroup>
        ))}
    </SectionFrame>
  )
}

/** A running instance with nothing that needs the user, pointing to where its work happens. */
function ReadyForWork({ name }: { name: string }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <BotIcon />
        </EmptyMedia>
        <EmptyTitle>Nothing needs you</EmptyTitle>
        <EmptyDescription>
          Start a thread with New thread above {name}&apos;s threads, or open Config to change what
          it does.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
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
    <section aria-labelledby={titleId} className="py-6">
      <Card>
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
  return (
    <SectionFrame
      title="Repository commands"
      lead={`What ${name} runs for the repository it works in. To change them, ask ${name} in a thread.`}
      variant="page"
    >
      <CommandLists held={status.repository_commands} missing={status.setup.missing_commands} />
    </SectionFrame>
  )
}

/** Each list held, by name with its commands in the order they run, then each missing one. */
function CommandLists({ held, missing }: { held: RepositoryCommandList[]; missing: string[] }) {
  return (
    <ItemGroup variant="outline" aria-label="Repository commands">
      {held.map(({ name, commands }) => (
        <li key={name} aria-label={name}>
          <Item size="sm">
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
        </li>
      ))}
      {missing.map((list) => (
        <li key={list} aria-label={list}>
          <Item size="sm">
            <ItemContent>
              <ItemTitle>
                <code className="font-mono">{list}</code>
              </ItemTitle>
            </ItemContent>
            <ItemActions>
              <Badge variant="outline">Missing</Badge>
            </ItemActions>
          </Item>
        </li>
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
