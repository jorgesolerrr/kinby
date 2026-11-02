import type { Client, Clock, HubSecret, HubSecretLink, HubSecretSetCommand } from "@kinby/contract"
import { CircleXIcon, KeyRoundIcon, MoreHorizontalIcon, PlusIcon } from "lucide-react"
import { type FormEvent, type ReactNode, useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { SectionFrame } from "@/components/section-frame"
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
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { useRead } from "@/hooks/use-read"
import { type Followed, followOperation, reason } from "@/lib/operation"

type Caller = Pick<Client, "call">

/** The one form open in the section: the add form, or Replace under the named secret. */
type Open = { form: "add" } | { form: "replace"; name: string }

/** The instances linked to a hub secret, each with the field it links. */
function linkedTo(links: HubSecretLink[]): string {
  return links.map((link) => `${link.manifest_id} · ${link.field}`).join(", ")
}

/** What a form saves with: the section's one save, whether it is under way, and why it failed. */
type Setting = Pick<ReturnType<typeof useSetSecret>, "save" | "saving" | "failed">

/** A form's first field takes the focus when the form opens. */
const focus = (input: HTMLInputElement | null) => input?.focus()

/**
 * The hub secrets by name with the instances linked to them, one row each. Add secret opens a form
 * at the top of the list, and a row's Replace a form under it, one form at a time. The section
 * follows the save, and opens no other form until it ends. No value is ever read back.
 */
export function HubSecretsSection({ client, clock }: { client: Caller; clock: Clock }) {
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
  const [open, setOpen] = useState<Open>()
  const saved = useCallback(() => {
    setOpen(undefined)
    readAgain()
  }, [readAgain])
  const { forget, ...setting } = useSetSecret(client, clock, saved)
  const show = (form?: Open) => {
    forget()
    setOpen(form)
  }
  const adding = open?.form === "add"
  const replacing = open?.form === "replace" ? open.name : undefined

  return (
    <SectionFrame
      title="Hub secrets"
      lead="Values the hub keeps so you set them once. It never shows one back."
      actions={
        <Button size="sm" disabled={adding || setting.saving} onClick={() => show({ form: "add" })}>
          <PlusIcon data-icon="inline-start" />
          Add secret
        </Button>
      }
    >
      {adding && <AddForm {...setting} onCancel={() => show()} />}
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined ? (
        failure === undefined && <Skeleton className="h-48 w-full" />
      ) : listed.secrets.length === 0 ? (
        !adding && (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <KeyRoundIcon />
              </EmptyMedia>
              <EmptyTitle>No hub secrets yet</EmptyTitle>
              <EmptyDescription>
                Add an API key or token once with Add secret, then link it to any instance that
                needs it.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )
      ) : (
        <ul aria-label="Hub secrets" className="flex flex-col divide-y">
          {listed.secrets.map((secret) => (
            <SecretRow
              key={secret.name}
              caller={client}
              secret={secret}
              replacing={replacing === secret.name}
              locked={setting.saving}
              onReplace={() =>
                show(replacing === secret.name ? undefined : { form: "replace", name: secret.name })
              }
              onChanged={readAgain}
            >
              <ReplaceForm
                {...setting}
                secret={secret}
                running={listed.running}
                onCancel={() => show()}
              />
            </SecretRow>
          ))}
        </ul>
      )}
    </SectionFrame>
  )
}

/**
 * Save a hub secret and follow the operation that saves it. `onSaved` runs once the hub holds the
 * value, and must keep its identity, or the save is sent again.
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
  const forget = useCallback(() => setRequest(undefined), [])
  return {
    save: setRequest,
    saving: request !== undefined && followed?.state !== "failed",
    failed: followed?.state === "failed" ? followed.detail : undefined,
    forget,
  }
}

/** A new secret's name and value, at the top of the list. */
function AddForm({ save, saving, failed, onCancel }: Setting & { onCancel: () => void }) {
  const id = useId()
  const [name, setName] = useState("")
  const [value, setValue] = useState("")

  const add = (event: FormEvent) => {
    event.preventDefault()
    save({ name, value })
  }

  return (
    <form
      aria-label="Add a secret"
      className="flex flex-col gap-3 rounded-xl border bg-muted/30 p-4"
      onSubmit={add}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
          <Input
            id={`${id}-name`}
            variant="mono"
            placeholder="NEW_SECRET_NAME"
            autoComplete="off"
            ref={focus}
            value={name}
            readOnly={saving}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field data-invalid={failed !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-value`}>Value</FieldLabel>
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
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={saving || name.trim() === "" || value === ""}>
          {saving && <Spinner data-icon="inline-start" />}
          Add secret
        </Button>
      </div>
    </form>
  )
}

/**
 * A secret's row: its name, the instances linked to it with their fields, Replace, and a menu with
 * Delete. Replace unfolds the row's form, `children`, under it, and a failed delete says why there.
 * A locked row opens nothing.
 */
function SecretRow({
  caller,
  secret,
  replacing,
  locked,
  onReplace,
  onChanged,
  children,
}: {
  caller: Caller
  secret: HubSecret
  replacing: boolean
  locked: boolean
  onReplace: () => void
  onChanged: () => void
  children: ReactNode
}) {
  const { name, links = [] } = secret
  const [asking, setAsking] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [failure, setFailure] = useState<unknown>()
  const linked = links.length === 0 ? "Not linked" : linkedTo(links)

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
    <li aria-label={name}>
      <Item size="row">
        <ItemMedia variant="icon">
          <KeyRoundIcon aria-hidden className="text-muted-foreground" />
        </ItemMedia>
        <ItemContent className="min-w-0">
          <ItemTitle className="max-w-full">
            <code className="min-w-0 truncate" title={name}>
              {name}
            </code>
          </ItemTitle>
          <ItemDescription lines="one" title={linked}>
            {linked}
          </ItemDescription>
        </ItemContent>
        <ItemActions>
          <Button
            variant="outline"
            size="sm"
            aria-expanded={replacing}
            disabled={locked}
            onClick={onReplace}
          >
            Replace
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`More for ${name}`}
                  disabled={deleting}
                />
              }
            >
              {deleting ? <Spinner /> : <MoreHorizontalIcon />}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem variant="destructive" onClick={() => setAsking(true)}>
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </ItemActions>
        {replacing && <ItemFooter>{children}</ItemFooter>}
        {failure !== undefined && (
          <ItemFooter>
            <Alert variant="destructive">
              <CircleXIcon />
              <AlertTitle>Could not delete {name}</AlertTitle>
              <AlertDescription>{reason(failure)}</AlertDescription>
            </Alert>
          </ItemFooter>
        )}
        <AlertDialog open={asking} onOpenChange={setAsking}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {name}?</AlertDialogTitle>
              <AlertDialogDescription>
                The hub deletes its copy. A token stays valid at its provider.
                {links.length > 0 &&
                  ` It unlinks ${linkedTo(links)}, which keep the value they hold.`}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={() => {
                  setAsking(false)
                  void remove()
                }}
              >
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </Item>
    </li>
  )
}

/**
 * Replace a secret's value, under its row. It offers to recreate the running linked instances, by
 * name. The value leaves the page once the hub holds it.
 */
function ReplaceForm({
  save,
  saving,
  failed,
  secret,
  running,
  onCancel,
}: Setting & {
  secret: HubSecret
  running: ReadonlySet<string>
  onCancel: () => void
}) {
  const id = useId()
  const { name, links = [] } = secret
  const [value, setValue] = useState("")
  const [recreating, setRecreating] = useState(true)
  const runningLinked = new Map(
    links
      .filter((link) => running.has(link.instance_id))
      .map((link) => [link.instance_id, link.manifest_id]),
  )

  const replace = (event: FormEvent) => {
    event.preventDefault()
    save({ name, value, recreate: recreating ? [...runningLinked.keys()] : [] })
  }

  return (
    <form
      aria-label={`Replace ${name}`}
      className="flex flex-1 flex-col gap-3 rounded-xl border bg-muted/30 p-4"
      onSubmit={replace}
    >
      <Field data-invalid={failed !== undefined || undefined}>
        <FieldLabel htmlFor={`${id}-value`}>New value</FieldLabel>
        <Input
          id={`${id}-value`}
          type="password"
          autoComplete="new-password"
          ref={focus}
          value={value}
          readOnly={saving}
          aria-invalid={failed !== undefined || undefined}
          onChange={(event) => setValue(event.target.value)}
        />
        {failed !== undefined && <FieldError errors={[{ message: failed }]} />}
      </Field>
      {runningLinked.size > 0 && (
        <Field orientation="horizontal">
          <Checkbox
            id={`${id}-recreate`}
            checked={recreating}
            disabled={saving}
            onCheckedChange={setRecreating}
          />
          <FieldLabel htmlFor={`${id}-recreate`}>
            Recreate {[...runningLinked.values()].join(", ")} now
          </FieldLabel>
        </Field>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={saving || value === ""}>
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
      </div>
    </form>
  )
}
