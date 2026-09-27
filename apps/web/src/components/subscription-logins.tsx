import type { Client, Clock, LoginSetup, LoginState } from "@kinby/contract"
import { useCallback, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { followLogin, type SignIn } from "@/lib/login"
import { CircleCheckIcon } from "lucide-react"

const noop = () => {}

/** How the hub last saw a login, before this page signed it in. Pending shows as nothing yet. */
const STORED: Record<LoginState, SignIn | undefined> = {
  pending: undefined,
  signed_in: { state: "signed-in" },
  failed: { state: "failed", detail: "The last sign-in did not finish." },
}

/**
 * One row per subscription login an instance declares, each signed in on its own and shown in
 * the state the hub keeps for it. `onEnded` hears each sign-in end, and must keep its identity.
 */
export function SubscriptionLogins({
  caller,
  clock,
  instanceId,
  logins,
  onEnded = noop,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  logins: LoginSetup[]
  onEnded?: () => void
}) {
  return (
    <ItemGroup aria-label="Subscription logins">
      {logins.map((login) => (
        <LoginRow
          key={login.id}
          caller={caller}
          clock={clock}
          instanceId={instanceId}
          login={login}
          onEnded={onEnded}
        />
      ))}
    </ItemGroup>
  )
}

interface LoginRequest {
  loginId: string
  running?: string
}

function LoginRow({
  caller,
  clock,
  instanceId,
  login,
  onEnded,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  login: LoginSetup
  onEnded: () => void
}) {
  // A new object for each click, so signing in again follows a new login. A sign-in the hub
  // already runs when the row loads, after a reload say, is followed without a click.
  const [request, setRequest] = useState<LoginRequest | undefined>(() =>
    login.operation_id ? { loginId: login.id, running: login.operation_id } : undefined,
  )
  const follow = useCallback(
    (asked: LoginRequest, report: (signIn: SignIn) => void) =>
      followLogin(
        caller,
        instanceId,
        asked.loginId,
        (signIn) => {
          report(signIn)
          if (signIn.state !== "signing-in") onEnded()
        },
        clock,
        asked.running,
      ),
    [caller, clock, instanceId, onEnded],
  )
  const followed =
    useFollowing(request, follow) ??
    (request === undefined ? undefined : { state: "signing-in", prompt: null })
  const signIn = followed ?? STORED[login.state]
  const busy = signIn?.state === "signing-in"
  return (
    <Item render={<li />} aria-label={login.label} variant="outline">
      <ItemContent>
        <ItemTitle>{login.label}</ItemTitle>
        <ItemDescription>{login.description}</ItemDescription>
        {signIn?.state === "signing-in" && signIn.prompt !== null && (
          <ItemDescription>
            Open{" "}
            <a href={signIn.prompt.url} target="_blank" rel="noreferrer">
              {signIn.prompt.url}
            </a>{" "}
            and enter <code className="font-mono text-foreground">{signIn.prompt.code}</code>
          </ItemDescription>
        )}
        {signIn?.state === "failed" && <ItemDescription>{signIn.detail}</ItemDescription>}
      </ItemContent>
      <ItemActions>
        <State signIn={signIn} />
        <Button variant="outline" disabled={busy} onClick={() => setRequest({ loginId: login.id })}>
          {signIn === undefined || busy ? "Sign in" : "Sign in again"}
        </Button>
      </ItemActions>
    </Item>
  )
}

function State({ signIn }: { signIn: SignIn | undefined }) {
  switch (signIn?.state) {
    case undefined:
      return <Badge variant="outline">Not signed in</Badge>
    case "signing-in":
      return (
        <Badge variant="secondary">
          <Spinner data-icon="inline-start" />
          Waiting
        </Badge>
      )
    case "signed-in":
      return (
        <Badge variant="success">
          <CircleCheckIcon data-icon="inline-start" />
          Signed in
        </Badge>
      )
    case "failed":
      return <Badge variant="destructive">Failed</Badge>
  }
}
