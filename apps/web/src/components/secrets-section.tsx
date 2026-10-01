import type { Client, Clock, InstanceSetup, SecretSetup } from "@kinby/contract"
import { useCallback, useId, useState } from "react"

import { SubscriptionLogins } from "@/components/subscription-logins"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
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
import { type Followed, followOperation } from "@/lib/operation"
import { CircleCheckIcon } from "lucide-react"

type Caller = Pick<Client, "call">

/**
 * The instance's secrets and subscription logins, as its status has them. `setup` is undefined
 * until the status is read. `onChanged` hears a secret set or a sign-in end, and must keep its
 * identity.
 */
export function SecretsSection({
  caller,
  clock,
  instanceId,
  setup,
  onChanged,
}: {
  caller: Caller
  clock: Clock
  instanceId: string
  setup: InstanceSetup | undefined
  onChanged: () => void
}) {
  if (setup === undefined) return <Skeleton className="h-48 w-full" />
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h2 className="font-medium">Secrets</h2>
        {setup.secrets.length > 0 ? (
          <Secrets
            caller={caller}
            clock={clock}
            instanceId={instanceId}
            secrets={setup.secrets}
            onSet={onChanged}
          />
        ) : (
          <p className="text-sm text-muted-foreground">The instance declares no secrets.</p>
        )}
      </section>
      {setup.logins.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="font-medium">Subscription logins</h2>
          <p className="text-sm text-muted-foreground">
            Sign in again when a subscription stops working.
          </p>
          <SubscriptionLogins
            caller={caller}
            clock={clock}
            instanceId={instanceId}
            logins={setup.logins}
            onEnded={onChanged}
          />
        </section>
      )}
    </div>
  )
}

/**
 * One row per secret the instance declares, with whether it holds a value, and Set or Replace.
 * No value is ever read back. `onSet` hears each value the hub wrote, and must keep its identity.
 */
export function Secrets({
  caller,
  clock,
  instanceId,
  secrets,
  onSet,
}: {
  caller: Caller
  clock: Clock
  instanceId: string
  secrets: SecretSetup[]
  onSet: () => void
}) {
  return (
    <ItemGroup aria-label="Secrets">
      {secrets.map((secret) => (
        <SecretRow
          key={secret.name}
          caller={caller}
          clock={clock}
          instanceId={instanceId}
          secret={secret}
          onSet={onSet}
        />
      ))}
    </ItemGroup>
  )
}

function SecretRow({
  caller,
  clock,
  instanceId,
  secret,
  onSet,
}: {
  caller: Caller
  clock: Clock
  instanceId: string
  secret: SecretSetup
  onSet: () => void
}) {
  const id = useId()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState("")
  // A new object for each save, so saving again follows a new operation.
  const [request, setRequest] = useState<{ value: string }>()
  const follow = useCallback(
    (asked: { value: string }, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () =>
          caller.call("instance.secrets.set", {
            instance_id: instanceId,
            secrets: { [secret.name]: asked.value },
          }),
        (followed) => {
          report(followed)
          if (followed.state !== "succeeded") return
          // The value leaves the page once the hub holds it.
          setEditing(false)
          setValue("")
          setRequest(undefined)
          onSet()
        },
        clock,
      ),
    [caller, clock, instanceId, secret.name, onSet],
  )
  const followed = useFollowing(request, follow)
  const saving = request !== undefined && followed?.state !== "failed"
  const close = () => {
    setEditing(false)
    setValue("")
    setRequest(undefined)
  }
  return (
    <Item render={<li />} aria-label={secret.label} variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>{secret.label}</ItemTitle>
        {secret.variable !== null && (
          <ItemDescription>
            <code className="font-mono">{secret.variable}</code>
          </ItemDescription>
        )}
      </ItemContent>
      <ItemActions>
        {!secret.required && <Badge variant="secondary">Optional</Badge>}
        {secret.is_set ? (
          <Badge variant="success">
            <CircleCheckIcon data-icon="inline-start" />
            Set
          </Badge>
        ) : (
          <Badge variant="outline">Not set</Badge>
        )}
        {!editing && (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            {secret.is_set ? "Replace" : "Set"}
          </Button>
        )}
      </ItemActions>
      {editing && (
        <ItemFooter>
          <form
            className="flex w-full flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              setRequest({ value })
            }}
          >
            <Field data-invalid={followed?.state === "failed" || undefined}>
              <FieldLabel htmlFor={id}>New value for {secret.label}</FieldLabel>
              <Input
                id={id}
                type="password"
                autoComplete="off"
                value={value}
                readOnly={saving}
                aria-invalid={followed?.state === "failed" || undefined}
                onChange={(event) => setValue(event.target.value)}
              />
              {followed?.state === "failed" && (
                <FieldError errors={[{ message: followed.detail }]} />
              )}
            </Field>
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={saving || value === ""}>
                {saving && <Spinner data-icon="inline-start" />}
                Save
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={saving} onClick={close}>
                Cancel
              </Button>
            </div>
          </form>
        </ItemFooter>
      )}
    </Item>
  )
}
