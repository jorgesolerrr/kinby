import type {
  Client,
  Clock,
  InstanceClient,
  InstanceStatusResult,
  InstanceSummary,
  McpServerFailed,
} from "@kinby/contract"
import { type ReactNode, useCallback, useEffect, useId, useState } from "react"

import { BrowserProfiles } from "@/components/browser-profiles"
import { PageLink, RunBadge } from "@/components/factories-page"
import { SectionFrame } from "@/components/section-frame"
import { Secrets } from "@/components/secrets-section"
import { McpSignInRow, McpSignIns } from "@/components/mcp-sign-ins"
import { LoginRow, SubscriptionLogins } from "@/components/subscription-logins"
import { Toolset } from "@/components/toolset"
import { Vault } from "@/components/vault"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { useRead } from "@/hooks/use-read"
import { workItem } from "@/lib/factory-runs"
import { instanceName, noticeKey, noticeTitle, observedState } from "@/lib/instances"
import { lineName, readFloor, unfinished } from "@/lib/lines"
import { linePath, openSkills } from "@/lib/selection"
import { BotIcon, CirclePauseIcon } from "lucide-react"

/**
 * An instance's Overview, under its header, which holds its name and lifecycle. A stopped instance
 * with setup pending opens on its setup card, and keeps it until it runs. Any other stopped one
 * says it is stopped. A running one opens on what needs the user: its logins not signed in, each
 * to sign in to, its MCP sign-ins not signed in, and its MCP servers that failed. Running or
 * stopped, it shows its toolset, and one the hub opens browsers for lists its
 * browser profiles and its vault. `onChanged` hears a sign-in or a secret end, so the instances
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
      <OwnedLines caller={caller} clock={clock} instanceId={instance.instance_id} name={name} />
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
    const signIns = (status?.setup.mcp_sign_ins ?? []).filter(
      (signIn) => signIn.state !== "signed_in",
    )
    const needsYou = logins.length > 0 || signIns.length > 0 || failed.length > 0
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
              {signIns.map((signIn) => (
                <McpSignInRow
                  key={signIn.server}
                  caller={caller}
                  instanceId={instance.instance_id}
                  signIn={signIn}
                  onChanged={onChanged}
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
        {sections}
        {!needsYou && observedState(instance) === "running" && <ReadyForWork name={name} />}
      </Sections>
    )
  }
  return null
}

/**
 * The lines the instance owns, each linking to its page, with how many runs it has unfinished and
 * the runs that wait on the user under it. Absent while the instance owns none, or when the lines
 * cannot be read.
 */
function OwnedLines({
  caller,
  clock,
  instanceId,
  name,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  name: string
}) {
  const read = useCallback(() => readFloor(caller), [caller])
  const floor = useRead(read, clock).value
  const owned = floor?.lines.filter(({ line }) => line.owner_id === instanceId) ?? []
  if (floor === undefined || owned.length === 0) return null
  return (
    <SectionFrame title="Lines" lead={`The factories ${name} looks after.`} variant="page">
      <ItemGroup variant="outline" aria-label="Lines">
        {owned.map(({ line, runs }) => {
          const lineTitle = lineName(line, floor.workbenches)
          return (
            <li key={line.line_id} aria-label={lineTitle}>
              <Item size="sm">
                <ItemContent>
                  <ItemTitle>
                    <PageLink href={linePath(line.line_id)} className="hover:underline">
                      {lineTitle}
                    </PageLink>
                  </ItemTitle>
                  <ItemDescription>{unfinished(runs).length} unfinished</ItemDescription>
                  {runs
                    .filter(({ needsYou }) => needsYou)
                    .map((held) => (
                      <PageLink
                        key={held.run.run_id}
                        href={linePath(line.line_id)}
                        className="flex items-center gap-2 hover:underline"
                      >
                        <RunBadge {...held} />
                        {workItem(held.run)}
                      </PageLink>
                    ))}
                </ItemContent>
              </Item>
            </li>
          )
        })}
      </ItemGroup>
    </SectionFrame>
  )
}

/** Overview's sections, one under the other. */
function Sections({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-8 py-6">{children}</div>
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
  const { logins, secrets, mcp_sign_ins: signIns = [] } = status.setup
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
            {signIns.length > 0 && (
              <section className="flex flex-col gap-3">
                <h3 className="font-medium">MCP sign-ins</h3>
                <McpSignIns
                  caller={caller}
                  instanceId={status.instance_id}
                  signIns={signIns}
                  onChanged={onChanged}
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
          </div>
        </CardContent>
      </Card>
    </section>
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
