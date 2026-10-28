import type { BrowserProfile, Client, Clock } from "@kinby/contract"
import { type FormEvent, useCallback, useId, useState } from "react"

import { LiveBrowser } from "@/components/live-browser"
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
import { Card, CardContent } from "@/components/ui/card"
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
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
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
import { reason } from "@/lib/operation"
import { CircleXIcon, PlusIcon, TextCursorInputIcon, Trash2Icon } from "lucide-react"

type Caller = Pick<Client, "call">
type Driver = Pick<Client, "call" | "subscribe">

/**
 * The instance's browser profiles, by name and site, each renamed or deleted here. New profile
 * opens a sign-in page in the instance's browser with no turn, where the user signs in live and
 * saves the page as a profile. `caller` reaches the hub, which keeps the profiles and the
 * browser, so this works while the instance is stopped.
 */
export function BrowserProfiles({
  caller,
  clock,
  instanceId,
}: {
  caller: Driver
  clock: Clock
  instanceId: string
}) {
  const titleId = useId()
  const list = useCallback(
    () => caller.call("browser.profile.list", { instance_id: instanceId }),
    [caller, instanceId],
  )
  const { value, failure, readAgain } = useRead(list, clock)
  const [creating, setCreating] = useState(false)
  const profiles = value?.profiles ?? []

  return (
    <section aria-labelledby={titleId} className="flex max-w-2xl flex-col gap-3 p-6">
      <div className="flex items-center gap-2">
        <h2 id={titleId} className="font-medium">
          Browser profiles
        </h2>
        {!creating && (
          <Button variant="outline" size="sm" className="ml-auto" onClick={() => setCreating(true)}>
            <PlusIcon data-icon="inline-start" />
            New profile
          </Button>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        Sites the instance opens signed in. The hub keeps their cookies, which the instance never
        reads.
      </p>
      {failure !== undefined && <Refusal title="Could not read the profiles" error={failure} />}
      {creating && (
        <NewProfile
          caller={caller}
          instanceId={instanceId}
          onSaved={() => {
            setCreating(false)
            readAgain()
          }}
          onCancel={() => setCreating(false)}
        />
      )}
      {profiles.length > 0 && (
        <ItemGroup aria-label="Browser profiles">
          {profiles.map((profile) => (
            <ProfileItem
              key={profile.name}
              caller={caller}
              instanceId={instanceId}
              profile={profile}
              onChanged={readAgain}
            />
          ))}
        </ItemGroup>
      )}
    </section>
  )
}

/** One profile, with its rename and its delete. */
function ProfileItem({
  caller,
  instanceId,
  profile,
  onChanged,
}: {
  caller: Caller
  instanceId: string
  profile: BrowserProfile
  onChanged: () => void
}) {
  const { name, site } = profile
  const [failure, setFailure] = useState<unknown>()
  const remove = () =>
    caller.call("browser.profile.delete", { instance_id: instanceId, name }).then(
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
        <RenameDialog
          name={name}
          onRename={(newName) =>
            caller
              .call("browser.profile.rename", { instance_id: instanceId, name, new_name: newName })
              .then(onChanged)
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
                The instance stops opening {site} signed in. Sign in again to make a new profile.
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

/** Asks for a profile's new name. A refusal, such as a name taken, shows in the dialog. */
function RenameDialog({
  name,
  onRename,
}: {
  name: string
  onRename: (newName: string) => Promise<unknown>
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [newName, setNewName] = useState("")
  const [renaming, setRenaming] = useState(false)
  const [failure, setFailure] = useState<unknown>()

  const rename = async () => {
    setRenaming(true)
    setFailure(undefined)
    try {
      await onRename(newName.trim())
      setOpen(false)
    } catch (error) {
      setFailure(error)
    } finally {
      setRenaming(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" variant="outline" aria-label={`Rename ${name}`} />}>
        <TextCursorInputIcon data-icon="inline-start" />
        Rename
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename {name}</DialogTitle>
          <DialogDescription>The instance opens it by its new name.</DialogDescription>
        </DialogHeader>
        {failure !== undefined && <Refusal title={`Could not rename ${name}`} error={failure} />}
        <Field>
          <FieldLabel htmlFor={id}>New name</FieldLabel>
          <Input
            id={id}
            value={newName}
            readOnly={renaming}
            onChange={(event) => setNewName(event.target.value)}
          />
        </Field>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
          <Button disabled={renaming || newName.trim() === ""} onClick={() => void rename()}>
            {renaming && <Spinner data-icon="inline-start" />}
            Rename
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Asks for the sign-in page, then opens it live for the user to sign in on. */
function NewProfile({
  caller,
  instanceId,
  onSaved,
  onCancel,
}: {
  caller: Driver
  instanceId: string
  onSaved: () => void
  onCancel: () => void
}) {
  const id = useId()
  const [url, setUrl] = useState("")
  const [opened, setOpened] = useState<{ takeoverId: string; url: string }>()

  if (opened !== undefined) {
    return (
      <SignIn
        caller={caller}
        instanceId={instanceId}
        takeover={opened}
        onSaved={onSaved}
        onCancel={onCancel}
      />
    )
  }
  const open = (event: FormEvent) => {
    event.preventDefault()
    setOpened({ takeoverId: crypto.randomUUID(), url: url.trim() })
  }
  return (
    <Card>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={open}>
          <Field>
            <FieldLabel htmlFor={id}>Sign-in page</FieldLabel>
            <Input
              id={id}
              type="url"
              value={url}
              placeholder="https://www.linkedin.com/login"
              onChange={(event) => setUrl(event.target.value)}
            />
            <FieldDescription>
              It opens in the instance&apos;s browser, for you to sign in.
            </FieldDescription>
          </Field>
          <div className="flex items-center gap-2">
            <Button type="submit" disabled={url.trim() === ""}>
              Open
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

/**
 * The takeover with no turn: the sign-in page live, and a name to save it under. Its browser
 * closes once saved, or when the user cancels or leaves the page.
 */
function SignIn({
  caller,
  instanceId,
  takeover,
  onSaved,
  onCancel,
}: {
  caller: Driver
  instanceId: string
  takeover: { takeoverId: string; url: string }
  onSaved: () => void
  onCancel: () => void
}) {
  const id = useId()
  const { takeoverId, url } = takeover
  const ids = { instance_id: instanceId, takeover_id: takeoverId }
  const watch = useCallback(
    () =>
      caller.subscribe("browser.takeover.start", {
        instance_id: instanceId,
        takeover_id: takeoverId,
        url,
      }),
    [caller, instanceId, takeoverId, url],
  )
  const [name, setName] = useState("")
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<unknown>()

  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      await caller.call("browser.takeover.finish", { ...ids, profile: name.trim() })
      onSaved()
    } catch (error) {
      setFailure(error)
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardContent>
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            Sign in on the page below, then name the profile and save it.
          </p>
          <LiveBrowser
            watch={watch}
            send={(input) => caller.call("browser.takeover.input", { ...ids, input })}
          />
          {failure !== undefined && <Refusal title="The profile was not saved" error={failure} />}
          <Field>
            <FieldLabel htmlFor={id}>Profile name</FieldLabel>
            <Input
              id={id}
              value={name}
              placeholder="LinkedIn"
              readOnly={saving}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <div className="flex items-center gap-2">
            <Button disabled={saving || name.trim() === ""} onClick={() => void save()}>
              {saving && <Spinner data-icon="inline-start" />}
              Save profile
            </Button>
            <Button variant="ghost" disabled={saving} onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function Refusal({ title, error }: { title: string; error: unknown }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{reason(error)}</AlertDescription>
    </Alert>
  )
}
