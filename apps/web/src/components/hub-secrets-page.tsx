import type { Client, Clock, HubSecret, HubSecretLink, HubSecretSetCommand } from "@kinby/contract"
import { CircleXIcon, KeyRoundIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { type FormEvent, Fragment, useCallback, useId, useState } from "react"

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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useFollowing } from "@/hooks/use-following"
import { useRead } from "@/hooks/use-read"
import { type Followed, followOperation, reason } from "@/lib/operation"

type Caller = Pick<Client, "call">

/** The instances linked to a hub secret, each with the field it links. */
function linkedNames(links: HubSecretLink[]): string {
  return links.map((link) => `${link.manifest_id} (${link.field})`).join(", ")
}

/**
 * The hub secrets by name with the instances linked to them, in one table whose first row adds a
 * secret. A row replaces its value in place, or deletes it. No value is ever read back.
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
  const [replacing, setReplacing] = useState<string>()

  return (
    <div className="flex flex-col gap-4 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-heading text-xl font-semibold">Secrets</h1>
        <p className="text-sm text-muted-foreground">
          Values the hub keeps so you set them once. The hub never shows one back.
        </p>
      </header>
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full" />
      ) : (
        <div className="rounded-lg border">
          <Table aria-label="Hub secrets">
            <TableHeader>
              <TableRow>
                <TableHead className="w-72">Name</TableHead>
                <TableHead>Linked to</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <AddRow caller={client} clock={clock} onAdded={readAgain} />
              {listed.secrets.length === 0 && (
                <TableRow>
                  <TableCell colSpan={3} className="text-center">
                    <span className="text-muted-foreground">The hub keeps no secrets yet.</span>
                  </TableCell>
                </TableRow>
              )}
              {listed.secrets.map((secret) => (
                <Fragment key={secret.name}>
                  <SecretRow
                    caller={client}
                    secret={secret}
                    replacing={replacing === secret.name}
                    onReplace={(open) => setReplacing(open ? secret.name : undefined)}
                    onChanged={readAgain}
                  />
                  {replacing === secret.name && (
                    <ReplaceRow
                      caller={client}
                      clock={clock}
                      secret={secret}
                      running={listed.running}
                      onDone={() => setReplacing(undefined)}
                      onSet={readAgain}
                    />
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

/**
 * Save a hub secret and follow the operation that saves it. `onSaved` runs once the hub holds the
 * value, and must keep its identity.
 */
function useSetSecret(caller: Caller, clock: Clock, onSaved: () => void) {
  // A new object for each save, so saving again follows a new operation.
  const [request, setRequest] = useState<HubSecretSetCommand>()
  const follow = useCallback(
    (asked: HubSecretSetCommand, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () => caller.call("hub.secret.set", asked),
        (followed) => {
          report(followed)
          if (followed.state !== "succeeded") return
          setRequest(undefined)
          onSaved()
        },
        clock,
      ),
    [caller, clock, onSaved],
  )
  const followed = useFollowing(request, follow)
  return {
    save: setRequest,
    saving: request !== undefined && followed?.state !== "failed",
    failed: followed?.state === "failed" ? followed.detail : undefined,
  }
}

/** The table's first row: a new secret's name and value, and Add secret. */
function AddRow({ caller, clock, onAdded }: { caller: Caller; clock: Clock; onAdded: () => void }) {
  const id = useId()
  const [name, setName] = useState("")
  const [value, setValue] = useState("")
  const added = useCallback(() => {
    setName("")
    setValue("")
    onAdded()
  }, [onAdded])
  const { save, saving, failed } = useSetSecret(caller, clock, added)

  const add = (event: FormEvent) => {
    event.preventDefault()
    save({ name, value })
  }

  return (
    <TableRow>
      <TableCell className="align-top">
        {/* A form cannot sit in a table row, so the row's fields name the form beside the table. */}
        <form id={`${id}-form`} onSubmit={add} />
        <Input
          form={`${id}-form`}
          aria-label="Name"
          placeholder="NEW_SECRET_NAME"
          autoComplete="off"
          value={name}
          readOnly={saving}
          onChange={(event) => setName(event.target.value)}
        />
      </TableCell>
      <TableCell className="align-top">
        <Field data-invalid={failed !== undefined || undefined}>
          <Input
            form={`${id}-form`}
            type="password"
            aria-label="Value"
            placeholder="Value"
            autoComplete="new-password"
            value={value}
            readOnly={saving}
            aria-invalid={failed !== undefined || undefined}
            onChange={(event) => setValue(event.target.value)}
          />
          {failed !== undefined && <FieldError errors={[{ message: failed }]} />}
        </Field>
      </TableCell>
      <TableCell className="text-right align-top">
        <Button
          type="submit"
          form={`${id}-form`}
          disabled={saving || name.trim() === "" || value === ""}
        >
          {saving ? <Spinner data-icon="inline-start" /> : <PlusIcon data-icon="inline-start" />}
          Add secret
        </Button>
      </TableCell>
    </TableRow>
  )
}

/** A secret's row: its name, the instances linked to it with their fields, Replace and Delete. */
function SecretRow({
  caller,
  secret,
  replacing,
  onReplace,
  onChanged,
}: {
  caller: Caller
  secret: HubSecret
  replacing: boolean
  onReplace: (open: boolean) => void
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
    <TableRow aria-label={name}>
      <TableCell>
        <span className="flex items-center gap-2 font-mono font-medium">
          <KeyRoundIcon aria-hidden className="size-4 text-muted-foreground" />
          {name}
        </span>
      </TableCell>
      <TableCell className="whitespace-normal">
        {links.length === 0 ? (
          <span className="text-muted-foreground">Not linked</span>
        ) : (
          <span className="flex flex-wrap gap-1.5">
            {links.map((link) => (
              <Badge key={`${link.instance_id} ${link.field}`} variant="outline">
                {link.manifest_id}
                <span className="font-mono font-normal text-muted-foreground">{link.field}</span>
              </Badge>
            ))}
          </span>
        )}
        {failure !== undefined && (
          <Alert variant="destructive" className="mt-2">
            <CircleXIcon />
            <AlertTitle>Could not delete {name}</AlertTitle>
            <AlertDescription>{reason(failure)}</AlertDescription>
          </Alert>
        )}
      </TableCell>
      <TableCell>
        <span className="flex justify-end gap-1">
          <Button
            variant="outline"
            size="sm"
            aria-expanded={replacing}
            onClick={() => onReplace(!replacing)}
          >
            Replace
          </Button>
          <AlertDialog>
            <AlertDialogTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Delete ${name}`}
                  disabled={deleting}
                />
              }
            >
              {deleting ? <Spinner /> : <Trash2Icon />}
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
        </span>
      </TableCell>
    </TableRow>
  )
}

/**
 * Replace a secret's value, in a row under its own. It offers to recreate the running linked
 * instances. The value leaves the page once the hub holds it.
 */
function ReplaceRow({
  caller,
  clock,
  secret,
  running,
  onDone,
  onSet,
}: {
  caller: Caller
  clock: Clock
  secret: HubSecret
  running: ReadonlySet<string>
  onDone: () => void
  onSet: () => void
}) {
  const id = useId()
  const { name, links = [] } = secret
  const [value, setValue] = useState("")
  const [recreating, setRecreating] = useState(true)
  const saved = useCallback(() => {
    onDone()
    onSet()
  }, [onDone, onSet])
  const { save, saving, failed } = useSetSecret(caller, clock, saved)
  // Replace opens with its value field focused, as the row the user asked to change.
  const focused = useCallback((input: HTMLInputElement | null) => input?.focus(), [])
  const runningLinked = [...new Set(links.map((link) => link.instance_id))].filter((instanceId) =>
    running.has(instanceId),
  )

  const replace = (event: FormEvent) => {
    event.preventDefault()
    save({ name, value, recreate: recreating ? runningLinked : [] })
  }

  return (
    <TableRow>
      <TableCell colSpan={3} className="whitespace-normal">
        <form
          aria-label={`Replace ${name}`}
          className="flex flex-wrap items-start gap-3 py-1.5"
          onSubmit={replace}
        >
          <Field className="max-w-sm" data-invalid={failed !== undefined || undefined}>
            <FieldLabel htmlFor={`${id}-value`} className="sr-only">
              New value
            </FieldLabel>
            <Input
              id={`${id}-value`}
              type="password"
              placeholder="New value"
              autoComplete="new-password"
              ref={focused}
              value={value}
              readOnly={saving}
              aria-invalid={failed !== undefined || undefined}
              onChange={(event) => setValue(event.target.value)}
            />
            {failed !== undefined && <FieldError errors={[{ message: failed }]} />}
          </Field>
          {runningLinked.length > 0 && (
            <Field orientation="horizontal" className="w-auto self-center">
              <Checkbox
                id={`${id}-recreate`}
                checked={recreating}
                disabled={saving}
                onCheckedChange={setRecreating}
              />
              <FieldLabel htmlFor={`${id}-recreate`}>Recreate the running ones now</FieldLabel>
            </Field>
          )}
          <span className="ml-auto flex gap-2">
            <Button type="button" variant="ghost" disabled={saving} onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving || value === ""}>
              {saving && <Spinner data-icon="inline-start" />}
              Save
            </Button>
          </span>
        </form>
      </TableCell>
    </TableRow>
  )
}
