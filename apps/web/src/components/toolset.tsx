import type {
  AuthoredTool,
  Client,
  Clock,
  DefaultTool,
  InstanceClient,
  McpServerName,
  SearchBackend,
  SearchSetup,
} from "@kinby/contract"
import { type ReactNode, useCallback, useId, useState } from "react"

import { Refusal } from "@/components/browser-profiles"
import { SectionFrame } from "@/components/section-frame"
import { Badge } from "@/components/ui/badge"
import { Field, FieldLabel } from "@/components/ui/field"
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item"
import { Switch } from "@/components/ui/switch"
import { useRead } from "@/hooks/use-read"

const BACKENDS: Record<SearchBackend, string> = {
  brave: "Brave",
  tavily: "Tavily",
  exa: "Exa",
  searxng: "SearXNG",
}

/**
 * What the instance can do beyond its workspace. `caller` reaches the hub, which reads it from the
 * instance's files, so it shows while the instance is stopped. An MCP server in `failed` failed
 * at the instance's last turn. An authored tool's read-only mark changes through `client`, the
 * instance's own contract, so only while it runs.
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
        <ul aria-label="Toolset" className="flex flex-col divide-y">
          <Row title="Default tools">
            <ItemDescription>{named("On", value.default_tools, true)}</ItemDescription>
            <ItemDescription>{named("Off", value.default_tools, false)}</ItemDescription>
          </Row>
          {value.mcp_servers.map((server) => (
            <Row
              key={server.name}
              title={server.name}
              actions={
                failed.includes(server.name) ? (
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
        </ul>
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
    <Item render={<li />} aria-label={title} size="row">
      <ItemContent>
        <ItemTitle>{title}</ItemTitle>
        {children}
      </ItemContent>
      {actions !== undefined && <ItemActions>{actions}</ItemActions>}
    </Item>
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
    <Item render={<li />} aria-label={tool.name} size="row">
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
  )
}

function named(state: string, tools: DefaultTool[], on: boolean): string {
  const names = tools.filter((tool) => tool.on === on).map((tool) => tool.name)
  return `${state}: ${names.join(", ") || "none"}`
}

function search({ backend, variable, is_set }: SearchSetup): string {
  const held = backend === "searxng" ? "Its server's URL" : "Its key"
  return `${BACKENDS[backend]}. ${held}, ${variable}, is ${is_set ? "set" : "not set"}.`
}
