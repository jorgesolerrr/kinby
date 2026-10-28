import type { Client, Clock, VaultEntry } from "@kinby/contract"
import { useCallback, useId, useState } from "react"

import { Refusal } from "@/components/browser-profiles"
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
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { useRead } from "@/hooks/use-read"
import { PencilIcon, PlusIcon, Trash2Icon } from "lucide-react"

type Caller = Pick<Client, "call">

/** What the user typed for an entry. A value left blank is null. */
interface Typed {
  name: string
  site: string
  username: string | null
  password: string | null
  totp_seed: string | null
}

/**
 * The instance's vault, by name and site, each entry added, edited or deleted here. The hub keeps
 * the values, so a saved one is never shown again.
 */
export function Vault({
  caller,
  clock,
  instanceId,
}: {
  caller: Caller
  clock: Clock
  instanceId: string
}) {
  const titleId = useId()
  const list = useCallback(
    () => caller.call("browser.vault.list", { instance_id: instanceId }),
    [caller, instanceId],
  )
  const { value, failure, readAgain } = useRead(list, clock)
  const entries = value?.entries ?? []

  const add = (typed: Typed) =>
    caller
      .call("browser.vault.add", {
        instance_id: instanceId,
        ...typed,
        username: typed.username ?? "",
        password: typed.password ?? "",
      })
      .then(readAgain)

  return (
    <section aria-labelledby={titleId} className="flex max-w-2xl flex-col gap-3 p-6">
      <div className="flex items-center gap-2">
        <h2 id={titleId} className="font-medium">
          Vault
        </h2>
        <EntryDialog
          onSave={add}
          trigger={
            <Button variant="outline" size="sm" className="ml-auto">
              <PlusIcon data-icon="inline-start" />
              Add sign-in
            </Button>
          }
        />
      </div>
      <p className="text-sm text-muted-foreground">
        Sign-ins the instance&apos;s browser fills, each on its own site only. The hub keeps their
        values, which the instance never reads.
      </p>
      {failure !== undefined && <Refusal title="Could not read the vault" error={failure} />}
      {entries.length > 0 && (
        <ItemGroup aria-label="Vault">
          {entries.map((entry) => (
            <EntryItem
              key={entry.name}
              caller={caller}
              instanceId={instanceId}
              entry={entry}
              onChanged={readAgain}
            />
          ))}
        </ItemGroup>
      )}
    </section>
  )
}

/** One entry, with its edit and its delete. */
function EntryItem({
  caller,
  instanceId,
  entry,
  onChanged,
}: {
  caller: Caller
  instanceId: string
  entry: VaultEntry
  onChanged: () => void
}) {
  const { name, site } = entry
  const [failure, setFailure] = useState<unknown>()
  const edit = (typed: Typed) =>
    caller.call("browser.vault.edit", { instance_id: instanceId, ...typed }).then(onChanged)
  const remove = () =>
    caller.call("browser.vault.delete", { instance_id: instanceId, name }).then(
      () => onChanged(),
      (error: unknown) => setFailure(error),
    )

  return (
    <Item render={<li />} aria-label={name} variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>{name}</ItemTitle>
        <ItemDescription>{site}</ItemDescription>
        {failure !== undefined && <Refusal title={`Could not delete ${name}`} error={failure} />}
      </ItemContent>
      <ItemActions>
        <EntryDialog
          entry={entry}
          onSave={edit}
          trigger={
            <Button size="sm" variant="outline" aria-label={`Edit ${name}`}>
              <PencilIcon data-icon="inline-start" />
              Edit
            </Button>
          }
        />
        <AlertDialog>
          <AlertDialogTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={`Delete ${name}`} />}
          >
            <Trash2Icon />
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {name}?</AlertDialogTitle>
              <AlertDialogDescription>
                The instance stops signing in to {site} with it.
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
    </Item>
  )
}

/**
 * Asks for an entry's sign-in: a new one, or a change to `entry`, whose saved values it never
 * shows. A refusal, such as a site that is not a web address, shows in the dialog.
 */
function EntryDialog({
  entry,
  onSave,
  trigger,
}: {
  entry?: VaultEntry
  onSave: (typed: Typed) => Promise<unknown>
  trigger: React.ReactElement
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [site, setSite] = useState("")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [seed, setSeed] = useState("")
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<unknown>()
  const adding = entry === undefined
  const complete = adding
    ? [name, site, username, password].every((typed) => typed.trim() !== "")
    : site.trim() !== ""

  const openChanged = (opened: boolean) => {
    setOpen(opened)
    setName("")
    setSite(entry?.site ?? "")
    setUsername("")
    setPassword("")
    setSeed("")
    setFailure(undefined)
  }
  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      await onSave({
        name: entry?.name ?? name.trim(),
        site: site.trim(),
        username: username === "" ? null : username,
        password: password === "" ? null : password,
        totp_seed: seed.trim() === "" ? null : seed.trim(),
      })
      setOpen(false)
    } catch (error) {
      setFailure(error)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={openChanged}>
      <DialogTrigger render={trigger} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{adding ? "Add a sign-in" : `Edit ${entry.name}`}</DialogTitle>
          <DialogDescription>
            {adding
              ? "The instance's browser fills it on the site only. Once saved, its values are never shown again."
              : "Saved values are never shown. Leave one blank to keep it."}
          </DialogDescription>
        </DialogHeader>
        {failure !== undefined && <Refusal title="The sign-in was not saved" error={failure} />}
        <FieldGroup>
          {adding && (
            <Field>
              <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
              <Input
                id={`${id}-name`}
                value={name}
                placeholder="LinkedIn"
                readOnly={saving}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
          )}
          <Field>
            <FieldLabel htmlFor={`${id}-site`}>Site</FieldLabel>
            <Input
              id={`${id}-site`}
              type="url"
              value={site}
              placeholder="https://www.linkedin.com"
              readOnly={saving}
              onChange={(event) => setSite(event.target.value)}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-username`}>Username</FieldLabel>
            <Input
              id={`${id}-username`}
              value={username}
              autoComplete="off"
              readOnly={saving}
              onChange={(event) => setUsername(event.target.value)}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-password`}>Password</FieldLabel>
            <Input
              id={`${id}-password`}
              type="password"
              value={password}
              autoComplete="new-password"
              readOnly={saving}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-seed`}>TOTP seed</FieldLabel>
            <Input
              id={`${id}-seed`}
              value={seed}
              autoComplete="off"
              readOnly={saving}
              onChange={(event) => setSeed(event.target.value)}
            />
            <FieldDescription>
              Optional. The setup key the site shows for an authenticator app.
            </FieldDescription>
          </Field>
        </FieldGroup>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
          <Button disabled={saving || !complete} onClick={() => void save()}>
            {saving && <Spinner data-icon="inline-start" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
