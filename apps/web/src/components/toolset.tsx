import type {
  AuthoredTool,
  BuiltInToolGroup,
  Client,
  Clock,
  GroupTool,
  InstanceClient,
  McpServerName,
  SearchBackend,
  SearchSetup,
  ToolState,
} from "@kinby/contract"
import { type ReactNode, useCallback, useId, useState } from "react"

import { Refusal } from "@/components/browser-profiles"
import { SignInState } from "@/components/mcp-sign-ins"
import { SectionFrame } from "@/components/section-frame"
import { Badge } from "@/components/ui/badge"
import { Field, FieldLabel } from "@/components/ui/field"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Switch } from "@/components/ui/switch"
import { useRead } from "@/hooks/use-read"
import { tokens } from "@/lib/stats"

const BACKENDS: Record<SearchBackend, string> = {
  brave: "Brave",
  tavily: "Tavily",
  exa: "Exa",
  searxng: "SearXNG",
}

const GROUPS: Record<BuiltInToolGroup, string> = {
  defaults: "Default tools",
  memory: "Memory tools",
  browser: "Browser tools",
  authored: "Authored tools",
}

const STATES: Record<ToolState, string> = {
  loaded: "Loaded",
  searchable: "Searchable",
  off: "Off",
}

const STATE_BADGES = {
  loaded: "secondary",
  searchable: "outline",
  off: "ghost",
} as const satisfies Record<ToolState, string>

/**
 * What the instance can do beyond its workspace. `caller` reaches the hub, which reads it from the
 * instance's files, so it shows while the instance is stopped. An MCP server in `failed` failed
 * at the instance's last turn, and one whose MCP sign-in is not done shows its sign-in's state
 * instead. An authored tool's read-only mark changes through `client`, the instance's own
 * contract, so only while it runs.
 */
export function Toolset({
  caller,
  client,
  clock,
  instanceId,
  failed,
}: {
  caller: Pick<Client, "call">
  client?: Pick<InstanceClient, "call">
  clock: Clock
  instanceId: string
  failed: McpServerName[]
}) {
  const read = useCallback(
    () => caller.call("instance.toolset.get", { instance_id: instanceId }),
    [caller, instanceId],
  )
  const { value, failure, readAgain } = useRead(read, clock)

  return (
    <SectionFrame
      title="Toolset"
      lead="What the instance can do beyond its workspace."
      variant="page"
    >
      {failure !== undefined && <Refusal title="Could not read the toolset" error={failure} />}
      {value !== undefined && (
        <ItemGroup variant="outline" aria-label="Tool groups">
          {value.built_in_groups.map((group) => (
            <ToolGroupItem
              key={group.group}
              title={GROUPS[group.group]}
              group={group}
              unlisted="Its tools are listed after the instance's next turn."
            />
          ))}
          {value.mcp_server_groups.map((group) => (
            <ToolGroupItem
              key={group.server}
              title={group.server}
              group={group}
              unlisted="Its tools are listed after the first turn connected to it."
            />
          ))}
        </ItemGroup>
      )}
      {value !== undefined && (
        <ItemGroup variant="outline" aria-label="Toolset">
          {value.mcp_servers.map((server) => (
            <Row
              key={server.name}
              title={server.name}
              actions={
                server.sign_in != null && server.sign_in !== "signed_in" ? (
                  <SignInState state={server.sign_in} />
                ) : failed.includes(server.name) ? (
                  <Badge variant="destructive">Failed</Badge>
                ) : (
                  <Badge variant="secondary">Ready</Badge>
                )
              }
            >
              <ItemDescription>
                {server.trust_hints ? "Read-only hints trusted." : "Every call to its tools asks."}
              </ItemDescription>
            </Row>
          ))}
          {value.authored_tools.map((tool) => (
            <AuthoredToolItem key={tool.name} client={client} tool={tool} onChanged={readAgain} />
          ))}
          <Row title="Web search">
            <ItemDescription>{search(value.search)}</ItemDescription>
          </Row>
          <Row title="Packages">
            <ItemDescription>{value.packages.join(", ") || "None."}</ItemDescription>
          </Row>
          <Row title="Browser">
            <ItemDescription>{value.browser ? "On" : "Off"}</ItemDescription>
          </Row>
        </ItemGroup>
      )}
    </SectionFrame>
  )
}

function Row({
  title,
  actions,
  children,
}: {
  title: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <li aria-label={title}>
      <Item size="sm">
        <ItemContent>
          <ItemTitle>{title}</ItemTitle>
          {children}
        </ItemContent>
        {actions !== undefined && <ItemActions>{actions}</ItemActions>}
      </Item>
    </li>
  )
}

/**
 * A tool group: its default state, what its loaded tools add to every model call, and its tools,
 * or `unlisted` until a turn lists them.
 */
function ToolGroupItem({
  title,
  group,
  unlisted,
}: {
  title: string
  group: { on: boolean; default_state: ToolState; loaded_tokens: number; tools: GroupTool[] | null }
  unlisted: string
}) {
  return (
    <li aria-label={title}>
      <Item size="sm">
        <ItemContent>
          <ItemTitle>{title}</ItemTitle>
          <ItemDescription>
            {group.on
              ? `${STATES[group.default_state]} by default. Its loaded tools add ${tokens(group.loaded_tokens)} tokens to every call.`
              : "Off."}
          </ItemDescription>
          {group.tools === null && <ItemDescription>{unlisted}</ItemDescription>}
        </ItemContent>
      </Item>
      {group.tools !== null && group.tools.length > 0 && (
        <ItemGroup aria-label={title} className="mx-3.5 mb-3 w-auto">
          {group.tools.map((tool) => (
            <li key={tool.name} aria-label={tool.name}>
              <Item size="xs" variant="muted">
                <ItemContent>
                  <ItemTitle>{tool.name}</ItemTitle>
                  <ItemDescription>{tool.description}</ItemDescription>
                </ItemContent>
                <ItemActions>
                  <Badge variant={STATE_BADGES[tool.state]}>{STATES[tool.state]}</Badge>
                </ItemActions>
              </Item>
            </li>
          ))}
        </ItemGroup>
      )}
    </li>
  )
}

/** An authored tool. Until it is marked read-only, every call to it asks. */
function AuthoredToolItem({
  client,
  tool,
  onChanged,
}: {
  client?: Pick<InstanceClient, "call">
  tool: AuthoredTool
  onChanged: () => void
}) {
  const id = useId()
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<unknown>()
  const mark = (read_only: boolean) => {
    if (client === undefined) return
    setBusy(true)
    setFailure(undefined)
    client
      .call("tool.read_only.set", { name: tool.name, read_only })
      .then(onChanged, (error: unknown) => setFailure(error))
      .finally(() => setBusy(false))
  }

  return (
    <li aria-label={tool.name}>
      <Item size="sm">
        <ItemContent>
          <ItemTitle>{tool.name}</ItemTitle>
          <ItemDescription>
            {tool.read_only ? "Runs as a read." : "Every call asks, as a write."}
          </ItemDescription>
        </ItemContent>
        <ItemActions>
          <Field orientation="horizontal" className="w-auto">
            <Switch
              id={id}
              checked={tool.read_only}
              disabled={client === undefined || busy}
              onCheckedChange={mark}
            />
            <FieldLabel htmlFor={id}>Read-only</FieldLabel>
          </Field>
        </ItemActions>
        {failure !== undefined && <Refusal title="Could not change the mark" error={failure} />}
      </Item>
    </li>
  )
}

function search({ backend, variable, is_set }: SearchSetup): string {
  const held = backend === "searxng" ? "Its server's URL" : "Its key"
  return `${BACKENDS[backend]}. ${held}, ${variable}, is ${is_set ? "set" : "not set"}.`
}
