import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { useCallback, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { instanceName, packageName } from "@/lib/instances"
import { type Followed, followOperation } from "@/lib/operation"
import { openHome, selectInstance } from "@/lib/selection"
import { ArchiveRestoreIcon, CircleXIcon } from "lucide-react"

/**
 * The hub's removed instances, each with Restore, or an empty state with a link home. A failed
 * restoration says why on its row. One that succeeds lists the instances again with `onRestored`,
 * waits for the lists, and opens the instance, which comes back stopped. `onRestored` must keep
 * its identity. A row being restored stays until its restoration ends, though the lists may drop
 * it first.
 */
export function RemovedInstancesPage({
  caller,
  clock,
  removed,
  onRestored,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  removed: InstanceSummary[] | undefined
  onRestored: () => Promise<void>
}) {
  const [restoring, setRestoring] = useState<InstanceSummary[]>([])
  const hold = useCallback(
    (instance: InstanceSummary) => setRestoring((held) => [...held, instance]),
    [],
  )
  const release = useCallback(
    (instanceId: string) =>
      setRestoring((held) => held.filter((instance) => instance.instance_id !== instanceId)),
    [],
  )
  if (removed === undefined) return null
  const rows = [
    ...removed,
    ...restoring.filter((held) => !removed.some((r) => r.instance_id === held.instance_id)),
  ]
  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-xl font-semibold">Removed instances</h1>
      {rows.length === 0 ? (
        <NothingRemoved />
      ) : (
        <ItemGroup className="max-w-2xl">
          {rows.map((instance) => (
            <RemovedRow
              key={instance.instance_id}
              caller={caller}
              clock={clock}
              instance={instance}
              onRestoring={hold}
              onFailed={release}
              onRestored={onRestored}
            />
          ))}
        </ItemGroup>
      )}
    </div>
  )
}

function NothingRemoved() {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <ArchiveRestoreIcon />
        </EmptyMedia>
        <EmptyTitle>No removed instances</EmptyTitle>
        <EmptyDescription>
          An instance you remove shows here, and you can restore it.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <a
          className={buttonVariants({ variant: "outline" })}
          href="/"
          onClick={(event) => {
            event.preventDefault()
            openHome()
          }}
        >
          Go home
        </a>
      </EmptyContent>
    </Empty>
  )
}

/** `onRestoring` hears Restore asked, and `onFailed` its restoration fail. */
function RemovedRow({
  caller,
  clock,
  instance,
  onRestoring,
  onFailed,
  onRestored,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  onRestoring: (instance: InstanceSummary) => void
  onFailed: (instanceId: string) => void
  onRestored: () => Promise<void>
}) {
  const name = instanceName(instance)
  const [request, setRequest] = useState<{ instanceId: string }>()
  const restore = useCallback(
    ({ instanceId }: { instanceId: string }, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () => caller.call("instance.restore", { instance_id: instanceId }),
        (followed) => {
          report(followed)
          if (followed.state === "failed") onFailed(instanceId)
          if (followed.state === "succeeded") {
            void onRestored().then(() => selectInstance(instanceId))
          }
        },
        clock,
      ),
    [caller, clock, onFailed, onRestored],
  )
  const followed = useFollowing(request, restore)
  const busy = request !== undefined && followed?.state !== "failed"
  return (
    <Item render={<li />} variant="outline">
      <ItemMedia>
        <InstanceAvatar avatar={instance.avatar} name={name} />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>{name}</ItemTitle>
        <ItemDescription>Package: {packageName(instance.package)}</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => {
            onRestoring(instance)
            setRequest({ instanceId: instance.instance_id })
          }}
        >
          {busy && <Spinner data-icon="inline-start" />}
          Restore
        </Button>
      </ItemActions>
      {followed?.state === "failed" && (
        <Alert variant="destructive" className="basis-full">
          <CircleXIcon />
          <AlertTitle>The restoration failed</AlertTitle>
          <AlertDescription>{followed.detail}</AlertDescription>
        </Alert>
      )}
    </Item>
  )
}
