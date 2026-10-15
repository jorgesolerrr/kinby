import type { Client, Clock, HubSecret, HubSecretLink, HubSecretSetCommand } from "@kinby/contract"
import { CircleXIcon, Trash2Icon } from "lucide-react"
import { type FormEvent, useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
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
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { useRead } from "@/hooks/use-read"
import { type Followed, followOperation, reason } from "@/lib/operation"

type Caller = Pick<Client, "call">

/** The instances linked to a hub secret, each with the field it links. */
function linkedNames(links: HubSecretLink[]): string {
  return links.map((link) => `${link.manifest_id} (${link.field})`).join(", ")
}

/**
 * The hub secrets by name with the instances linked to them, to add, replace or delete. No value
 * is ever read back.
 */
export function HubSecretsPage({ client, clock }: { client: Caller; clock: Clock }) {
  const list = useCallback(async () => {
    const [{ secrets }, { instances }] = await Promise.all([
      client.call("hub.secret.list", {}),
      client.call("instance.list", {}),
    ])
    const running = new Set(
      instances
        .filter((instance) => instance.intended_state === "running")
        .map((instance) => instance.instance_id),
    )
    return { secrets, running }
  }, [client])
  const { value: listed, failure, readAgain } = useRead(list, clock)

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex max-w-2xl items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Secrets</h1>
        <SetDialog caller={client} clock={clock} onSet={readAgain} />
      </div>
      <p className="max-w-2xl text-sm text-muted-foreground">
        Values the hub keeps so you set them once. The hub never shows one back.
      </p>
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full max-w-2xl" />
      ) : listed.secrets.length === 0 ? (
        <p className="text-sm text-muted-foreground">The hub keeps no secrets yet.</p>
      ) : (
        <ItemGroup aria-label="Hub secrets" className="max-w-2xl">
          {listed.secrets.map((secret) => (
            <SecretItem
              key={secret.name}
              caller={client}
              clock={clock}
              secret={secret}
              running={listed.running}
              onChanged={readAgain}
            />
          ))}
        </ItemGroup>
      )}
    </div>
  )
}

function SecretItem({
  caller,
  clock,
  secret,
  running,
  onChanged,
}: {
  caller: Caller
  clock: Clock
  secret: HubSecret
  running: ReadonlySet<string>
  onChanged: () => void
}) {
  const { name, links = [] } = secret
  const [deleting, setDeleting] = useState(false)
  const [failure, setFailure] = useState<unknown>()

  const remove = async () => {
    setDeleting(true)
    setFailure(undefined)
    try {
      await caller.call("hub.secret.delete", { name })
      onChanged()
    } catch (error) {
      setFailure(error)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Item render={<li />} aria-label={name} variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>{name}</ItemTitle>
        {links.length > 0 && <ItemDescription>Linked to {linkedNames(links)}</ItemDescription>}
      </ItemContent>
      <ItemActions>
        <SetDialog
          caller={caller}
          clock={clock}
          replacing={{ name, links, running }}
          onSet={onChanged}
        />
        <AlertDialog>
          <AlertDialogTrigger render={<Button variant="outline" size="sm" disabled={deleting} />}>
            {deleting ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <Trash2Icon data-icon="inline-start" />
            )}
            Delete
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {name}?</AlertDialogTitle>
              <AlertDialogDescription>
                The hub deletes its copy. A token stays valid at its provider.
                {links.length > 0 &&
                  ` It unlinks ${linkedNames(links)}, which keep the value they hold.`}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction variant="destructive" onClick={() => void remove()}>
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </ItemActions>
      {failure !== undefined && (
        <ItemFooter>
          <Alert variant="destructive">
            <CircleXIcon />
            <AlertTitle>Could not delete {name}</AlertTitle>
            <AlertDescription>{reason(failure)}</AlertDescription>
          </Alert>
        </ItemFooter>
      )}
    </Item>
  )
}

/** The hub secret a dialog replaces, the instances linked to it, and which instances run. */
interface Replacing {
  name: string
  links: HubSecretLink[]
  running: ReadonlySet<string>
}

/**
 * Add a hub secret from its name and value, or replace the value of the one in `replacing`. A
 * replacement offers to recreate the running linked instances. The value leaves the page once the
 * hub holds it. `onSet` must keep its identity.
 */
function SetDialog({
  caller,
  clock,
  replacing,
  onSet,
}: {
  caller: Caller
  clock: Clock
  replacing?: Replacing
  onSet: () => void
}) {
  const id = useId()
  const replaced = replacing?.name
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(replaced ?? "")
  const [value, setValue] = useState("")
  const [recreating, setRecreating] = useState(true)
  // A new object for each save, so saving again follows a new operation.
  const [request, setRequest] = useState<HubSecretSetCommand>()
  const reset = useCallback(
    (opened: boolean) => {
      setOpen(opened)
      setName(replaced ?? "")
      setValue("")
      setRecreating(true)
      setRequest(undefined)
    },
    [replaced],
  )
  const runningLinked =
    replacing === undefined
      ? []
      : [...new Set(replacing.links.map((link) => link.instance_id))].filter((instanceId) =>
          replacing.running.has(instanceId),
        )
  const follow = useCallback(
    (asked: HubSecretSetCommand, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () => caller.call("hub.secret.set", asked),
        (followed) => {
          report(followed)
          if (followed.state !== "succeeded") return
          reset(false)
          onSet()
        },
        clock,
      ),
    [caller, clock, reset, onSet],
  )
  const followed = useFollowing(request, follow)
  const saving = request !== undefined && followed?.state !== "failed"
  const failed = followed?.state === "failed" ? followed.detail : undefined

  const save = (event: FormEvent) => {
    event.preventDefault()
    setRequest(
      replacing === undefined
        ? { name, value }
        : { name, value, recreate: recreating ? runningLinked : [] },
    )
  }

  return (
    <Dialog open={open} onOpenChange={(opened) => !saving && reset(opened)}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        {replacing === undefined ? "Add a secret" : "Replace"}
      </DialogTrigger>
      <DialogContent>
        <form className="flex flex-col gap-4" onSubmit={save}>
          <DialogHeader>
            <DialogTitle>
              {replacing === undefined ? "Add a hub secret" : `Replace ${replaced}`}
            </DialogTitle>
            <DialogDescription>
              {replacing === undefined
                ? "Its name is how you pick it. Its value is never shown back."
                : replacing.links.length === 0
                  ? "The new value takes the old one's place."
                  : `The new value takes the old one's place in ${linkedNames(replacing.links)}.`}
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            {replacing === undefined && (
              <Field>
                <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
                <Input
                  id={`${id}-name`}
                  autoComplete="off"
                  value={name}
                  readOnly={saving}
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>
            )}
            <Field data-invalid={failed !== undefined || undefined}>
              <FieldLabel htmlFor={`${id}-value`}>
                {replacing === undefined ? "Value" : "New value"}
              </FieldLabel>
              <Input
                id={`${id}-value`}
                type="password"
                autoComplete="new-password"
                value={value}
                readOnly={saving}
                aria-invalid={failed !== undefined || undefined}
                onChange={(event) => setValue(event.target.value)}
              />
              {failed !== undefined && <FieldError errors={[{ message: failed }]} />}
            </Field>
            {runningLinked.length > 0 && (
              <Field orientation="horizontal">
                <Checkbox
                  id={`${id}-recreate`}
                  checked={recreating}
                  disabled={saving}
                  onCheckedChange={setRecreating}
                />
                <FieldLabel htmlFor={`${id}-recreate`}>Recreate the running ones now</FieldLabel>
              </Field>
            )}
          </FieldGroup>
          <DialogFooter>
            <DialogClose render={<Button type="button" variant="outline" disabled={saving} />}>
              Cancel
            </DialogClose>
            <Button type="submit" disabled={saving || name.trim() === "" || value === ""}>
              {saving && <Spinner data-icon="inline-start" />}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
