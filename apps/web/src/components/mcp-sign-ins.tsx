import type { Client, LoginState, McpSignInSetup } from "@kinby/contract"
import { type ComponentProps, useState } from "react"

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
import { reason } from "@/lib/operation"
import { CircleCheckIcon } from "lucide-react"

type Caller = Pick<Client, "call">

/**
 * One row per picked MCP server that asks for a sign-in, in the state the hub keeps for it, each
 * signed in or out on its own. `onChanged` hears each sign-out, and each sign-in that did not start.
 */
export function McpSignIns({
  caller,
  instanceId,
  signIns,
  onChanged,
}: {
  caller: Caller
  instanceId: string
  signIns: McpSignInSetup[]
  onChanged: () => void
}) {
  return (
    <ItemGroup aria-label="MCP sign-ins">
      {signIns.map((signIn) => (
        <McpSignInRow
          key={signIn.server}
          caller={caller}
          instanceId={instanceId}
          signIn={signIn}
          onChanged={onChanged}
        />
      ))}
    </ItemGroup>
  )
}

/**
 * One MCP sign-in's row. Sign in leaves for the server's page, which sends the browser back to
 * the instance's page. An outline row by default; `variant` and `size` set it for a bordered row
 * group.
 */
export function McpSignInRow({
  caller,
  instanceId,
  signIn,
  onChanged,
  variant = "outline",
  size,
}: {
  caller: Caller
  instanceId: string
  signIn: McpSignInSetup
  onChanged: () => void
} & Pick<ComponentProps<typeof Item>, "variant" | "size">) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  // What the hub answered a sign-out with, for a page that does not read its state again.
  const [answered, setAnswered] = useState<McpSignInSetup>()
  const shown = answered ?? signIn
  const target = { instance_id: instanceId, server: signIn.server }

  const signInNow = async () => {
    setBusy(true)
    setError(undefined)
    try {
      const started = await caller.call("instance.mcp_sign_in.start", {
        ...target,
        origin: window.location.origin,
      })
      window.location.assign(started.authorization_url)
    } catch (failed) {
      setError(reason(failed))
      setBusy(false)
      onChanged()
    }
  }

  const signOut = async () => {
    setBusy(true)
    setError(undefined)
    try {
      setAnswered(await caller.call("instance.mcp_sign_in.sign_out", target))
      onChanged()
    } catch (failed) {
      setError(reason(failed))
    } finally {
      setBusy(false)
    }
  }

  const detail = error ?? (shown.state === "failed" ? shown.detail : undefined)
  return (
    <li aria-label={`MCP server ${signIn.server}`}>
      <Item variant={variant} size={size}>
        {/* On a phone the text takes the whole row and the badge and button wrap below it. */}
        <ItemContent className="basis-full sm:basis-0">
          <ItemTitle>
            <code className="font-mono">{signIn.server}</code>
          </ItemTitle>
          <ItemDescription>The MCP server asks you to sign in.</ItemDescription>
          {detail && <ItemDescription lines="all">{detail}</ItemDescription>}
        </ItemContent>
        <ItemActions>
          <SignInState state={shown.state} />
          {shown.state === "signed_in" ? (
            <Button variant="outline" disabled={busy} onClick={signOut}>
              Sign out
            </Button>
          ) : (
            <Button variant="outline" disabled={busy} onClick={signInNow}>
              Sign in
            </Button>
          )}
        </ItemActions>
      </Item>
    </li>
  )
}

export function SignInState({ state }: { state: LoginState }) {
  switch (state) {
    case "pending":
      return <Badge variant="outline">Not signed in</Badge>
    case "signed_in":
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
