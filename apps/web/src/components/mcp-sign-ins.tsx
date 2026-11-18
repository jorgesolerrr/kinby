import type { LoginState, McpSignInSetup } from "@kinby/contract"
import type { ComponentProps } from "react"

import { Badge } from "@/components/ui/badge"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { CircleCheckIcon } from "lucide-react"

/** One row per picked MCP server that asks for a sign-in, in the state the hub keeps for it. */
export function McpSignIns({ signIns }: { signIns: McpSignInSetup[] }) {
  return (
    <ItemGroup aria-label="MCP sign-ins">
      {signIns.map((signIn) => (
        <McpSignInRow key={signIn.server} signIn={signIn} />
      ))}
    </ItemGroup>
  )
}

/**
 * One MCP sign-in's row. An outline row by default; `variant` and `size` set it for a bordered
 * row group.
 */
export function McpSignInRow({
  signIn,
  variant = "outline",
  size,
}: { signIn: McpSignInSetup } & Pick<ComponentProps<typeof Item>, "variant" | "size">) {
  return (
    <li aria-label={`MCP server ${signIn.server}`}>
      <Item variant={variant} size={size}>
        <ItemContent>
          <ItemTitle>
            <code className="font-mono">{signIn.server}</code>
          </ItemTitle>
          <ItemDescription>The MCP server asks you to sign in.</ItemDescription>
        </ItemContent>
        <ItemActions>
          <SignInState state={signIn.state} />
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
