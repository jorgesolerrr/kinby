import type {
  Client,
  Clock,
  InstanceDeleteCommand,
  InstanceDeletePreviewResult,
  InstanceSummary,
} from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { OperationSteps } from "@/components/operation-steps"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
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
import { type Deleting, followDeletion } from "@/lib/deletion"
import { instanceName, packageName } from "@/lib/instances"
import { type Followed, followOperation, reason } from "@/lib/operation"
import { openHome, selectInstance } from "@/lib/selection"
import { ArchiveRestoreIcon, CircleXIcon, TriangleAlertIcon } from "lucide-react"

/**
 * The hub's removed instances, each with Restore and Delete permanently, or an empty state with a
 * link home. A failed restoration says why on its row. One that succeeds lists the instances again
 * with `onChanged`, waits for the lists, and opens the instance, which comes back stopped. A
 * deletion that succeeds lists them again too, and the row leaves. `onChanged` must keep its
 * identity. A row being restored stays until its restoration ends, though the lists may drop it
 * first.
 */
export function RemovedInstancesPage({
  caller,
  clock,
  removed,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  removed: InstanceSummary[] | undefined
  onChanged: () => Promise<void>
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
              onChanged={onChanged}
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
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  onRestoring: (instance: InstanceSummary) => void
  onFailed: (instanceId: string) => void
  onChanged: () => Promise<void>
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
            void onChanged().then(() => selectInstance(instanceId))
          }
        },
        clock,
      ),
    [caller, clock, onFailed, onChanged],
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
        <AlertDialog>
          <AlertDialogTrigger render={<Button variant="destructive" />}>
            Delete permanently
          </AlertDialogTrigger>
          <AlertDialogContent className="sm:max-w-lg">
            <Deletion
              caller={caller}
              clock={clock}
              instanceId={instance.instance_id}
              name={name}
              onDeleted={onChanged}
            />
          </AlertDialogContent>
        </AlertDialog>
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

/**
 * The deletion dialog's body. It mounts each time the dialog opens, so every deletion starts from
 * a fresh preview.
 */
function Deletion({
  caller,
  clock,
  instanceId,
  name,
  onDeleted,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  name: string
  onDeleted: () => Promise<void>
}) {
  const typedId = useId()
  const [preview, setPreview] = useState<InstanceDeletePreviewResult>()
  const [previewFailure, setPreviewFailure] = useState<string>()
  const [changed, setChanged] = useState(false)
  const [typed, setTyped] = useState("")
  const [request, setRequest] = useState<InstanceDeleteCommand>()
  const follow = useCallback(
    (command: InstanceDeleteCommand, report: (deleting: Deleting) => void) =>
      followDeletion(
        caller,
        command,
        (deleting) => {
          report(deleting)
          if (deleting.state === "deleted") void onDeleted()
          if (deleting.state === "changed") {
            setPreview(deleting.preview)
            setChanged(true)
            setTyped("")
            setRequest(undefined)
          }
        },
        clock,
      ),
    [caller, clock, onDeleted],
  )
  const deleting = useFollowing(request, follow)
  useEffect(() => {
    let current = true
    caller.call("instance.delete.preview", { instance_id: instanceId }).then(
      (previewed) => {
        if (current) setPreview(previewed)
      },
      (error: unknown) => {
        if (current) setPreviewFailure(reason(error))
      },
    )
    return () => {
      current = false
    }
  }, [caller, instanceId])
  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>Delete {name} permanently</AlertDialogTitle>
        <AlertDialogDescription>
          The hub deletes these directories and volumes, and the deletion can't be undone.
        </AlertDialogDescription>
      </AlertDialogHeader>
      {changed && (
        <Alert>
          <TriangleAlertIcon />
          <AlertTitle>What would be deleted changed</AlertTitle>
          <AlertDescription>
            The hub deleted nothing. Read the list again, then type the name to confirm it.
          </AlertDescription>
        </Alert>
      )}
      {previewFailure !== undefined && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The hub did not preview the deletion</AlertTitle>
          <AlertDescription>{previewFailure}</AlertDescription>
        </Alert>
      )}
      {preview !== undefined && (
        <>
          <Targets label="Directories" targets={preview.directories} />
          <Targets label="Volumes" targets={preview.volumes} />
        </>
      )}
      <Field>
        <FieldLabel htmlFor={typedId}>Type {name} to confirm</FieldLabel>
        <Input
          id={typedId}
          autoComplete="off"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
      </Field>
      {(deleting?.state === "deleting" || deleting?.state === "failed") && (
        <OperationSteps label="Deletion steps" steps={deleting.steps} />
      )}
      {deleting?.state === "failed" && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The deletion failed</AlertTitle>
          <AlertDescription>
            <p>{deleting.detail}</p>
            <p>What is left stays. Delete permanently again to retry from a fresh preview.</p>
          </AlertDescription>
        </Alert>
      )}
      <AlertDialogFooter>
        <AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction
          variant="destructive"
          disabled={preview === undefined || typed !== name || request !== undefined}
          onClick={() => {
            setChanged(false)
            setRequest(preview)
          }}
        >
          Delete permanently
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  )
}

function Targets({ label, targets }: { label: string; targets: string[] }) {
  if (targets.length === 0) return null
  return (
    <section className="flex flex-col gap-1 text-sm">
      <h3 className="font-medium">{label}</h3>
      <ul aria-label={label} className="font-mono break-all text-muted-foreground">
        {targets.map((target) => (
          <li key={target}>{target}</li>
        ))}
      </ul>
    </section>
  )
}
