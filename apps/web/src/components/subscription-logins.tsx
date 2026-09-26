import type { Client, Clock, SubscriptionLogin } from "@kinby/contract"
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

/** One row per subscription login an instance declares, each signed in on its own. */
export function SubscriptionLogins({
  caller,
  clock,
  instanceId,
  logins,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  logins: SubscriptionLogin[]
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
        />
      ))}
    </ItemGroup>
  )
}

function LoginRow({
  caller,
  clock,
  instanceId,
  login,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  login: SubscriptionLogin
}) {
  // A new object for each click, so signing in again follows a new login.
  const [request, setRequest] = useState<{ loginId: string }>()
  const follow = useCallback(
    (asked: { loginId: string }, report: (signIn: SignIn) => void) =>
      followLogin(caller, instanceId, asked.loginId, report, clock),
    [caller, clock, instanceId],
  )
  const signIn =
    useFollowing(request, follow) ??
    (request === undefined ? undefined : { state: "signing-in", prompt: null })
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
