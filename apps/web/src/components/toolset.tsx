import type {
  AuthoredTool,
  Client,
  Clock,
  DefaultTool,
  InstanceClient,
  SearchBackend,
  SearchSetup,
} from "@kinby/contract"
import { type ReactNode, useCallback, useId, useState } from "react"

import { Refusal } from "@/components/browser-profiles"
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

const BACKENDS: Record<SearchBackend, string> = {
  brave: "Brave",
  tavily: "Tavily",
  exa: "Exa",
  searxng: "SearXNG",
}

/**
 * What the instance can do beyond its workspace. `caller` reaches the hub, which reads it from the
 * instance's files, so it shows while the instance is stopped. An authored tool's read-only mark
 * changes through `client`, the instance's own contract, so only while it runs.
 */
export function Toolset({
  caller,
  client,
  clock,
  instanceId,
}: {
  caller: Pick<Client, "call">
  client?: Pick<InstanceClient, "call">
  clock: Clock
  instanceId: string
}) {
  const titleId = useId()
  const read = useCallback(
    () => caller.call("instance.toolset.get", { instance_id: instanceId }),
    [caller, instanceId],
  )
  const { value, failure, readAgain } = useRead(read, clock)

  return (
    <section aria-labelledby={titleId} className="flex max-w-2xl flex-col gap-3 p-6">
      <h2 id={titleId} className="text-base font-semibold">
        Toolset
      </h2>
      <p className="text-sm text-muted-foreground">
        What the instance can do beyond its workspace.
      </p>
      {failure !== undefined && <Refusal title="Could not read the toolset" error={failure} />}
      {value !== undefined && (
        <>
          <ItemGroup aria-label="Toolset">
            <Row title="Default tools">
              <ItemDescription>{named("On", value.default_tools, true)}</ItemDescription>
              <ItemDescription>{named("Off", value.default_tools, false)}</ItemDescription>
            </Row>
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
          <h3 className="text-sm font-medium">MCP servers</h3>
          {value.mcp_servers.length === 0 ? (
            <p className="text-sm text-muted-foreground">None picked.</p>
          ) : (
            <ItemGroup aria-label="MCP servers">
              {value.mcp_servers.map((server) => (
                <Row key={server.name} title={server.name}>
                  <ItemDescription>
                    {server.trust_hints
                      ? "Read-only hints trusted."
                      : "Every call to its tools asks."}
                  </ItemDescription>
                </Row>
              ))}
            </ItemGroup>
          )}
          <h3 className="text-sm font-medium">Authored tools</h3>
          {value.authored_tools.length === 0 ? (
            <p className="text-sm text-muted-foreground">None yet.</p>
          ) : (
            <ItemGroup aria-label="Authored tools">
              {value.authored_tools.map((tool) => (
                <AuthoredToolItem
                  key={tool.name}
                  client={client}
                  tool={tool}
                  onChanged={readAgain}
                />
              ))}
            </ItemGroup>
          )}
        </>
      )}
    </section>
  )
}

function Row({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Item render={<li />} aria-label={title} variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>{title}</ItemTitle>
        {children}
      </ItemContent>
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
    <Item render={<li />} aria-label={tool.name} variant="outline" size="sm">
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
