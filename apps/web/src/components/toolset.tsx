import type {
  AuthoredTool,
  BuiltInToolGroup,
  Client,
  Clock,
  GroupTool,
  InstanceClient,
  InstanceToolsetResult,
  InstanceToolStatesSetCommand,
  McpServerName,
  SearchBackend,
  SearchSetup,
  ToolGroupStates,
  ToolState,
} from "@kinby/contract"
import { type ReactNode, useCallback, useId, useState } from "react"

import { Refusal } from "@/components/browser-profiles"
import { SignInState } from "@/components/mcp-sign-ins"
import { SectionFrame } from "@/components/section-frame"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useRead } from "@/hooks/use-read"
import { TOOL_STATES } from "@/lib/mcp-servers"
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

/**
 * What the instance can do beyond its workspace. `caller` reaches the hub, which reads it from the
 * instance's files, so it shows while the instance is stopped, and changes its tool states, which
 * its next turn takes. An MCP server in `failed` failed at the instance's last turn, and one whose
 * MCP sign-in is not done shows its sign-in's state instead. An authored tool's read-only mark
 * changes through `client`, the instance's own contract, so only while it runs.
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
  const { value: lastRead, failure, readAgain } = useRead(read, clock)
  // A set returns the toolset, with its new hash, which stands until the next read.
  const [lastSet, setLastSet] = useState<{
    over: InstanceToolsetResult | undefined
    toolset: InstanceToolsetResult
  }>()
  const value = lastSet !== undefined && lastSet.over === lastRead ? lastSet.toolset : lastRead
  const [saving, setSaving] = useState(false)
  const [refusal, setRefusal] = useState<unknown>()
  const setStates = (
    groups: Pick<InstanceToolStatesSetCommand, "built_in_groups" | "mcp_servers">,
  ) => {
    if (value === undefined) return
    setSaving(true)
    setRefusal(undefined)
    caller
      .call("instance.tool_states.set", { instance_id: instanceId, ...groups, hash: value.hash })
      .then(
        (toolset) => setLastSet({ over: lastRead, toolset }),
        (error: unknown) => {
          setRefusal(error)
          readAgain()
        },
      )
      .finally(() => setSaving(false))
  }

  return (
    <SectionFrame
      title="Toolset"
      lead="What the instance can do beyond its workspace."
      variant="page"
    >
      {failure !== undefined && <Refusal title="Could not read the toolset" error={failure} />}
      {refusal !== undefined && (
        <Refusal title="Could not change the tool states" error={refusal} />
      )}
      {value !== undefined && (
        <ItemGroup variant="outline" aria-label="Tool groups">
          {value.built_in_groups.map((group) => (
            <ToolGroupItem
              key={group.group}
              title={GROUPS[group.group]}
              group={group}
              unlisted="Its tools are listed after the instance's next turn."
              owner="kinby's"
              disabled={saving}
              onChange={(states) => setStates({ built_in_groups: { [group.group]: states } })}
            />
          ))}
          {value.mcp_server_groups.map((group) => (
            <ToolGroupItem
              key={group.server}
              title={group.server}
              group={group}
              unlisted="Its tools are listed after the first turn connected to it."
              owner="the library's"
              disabled={saving}
              onChange={(states) => setStates({ mcp_servers: { [group.server]: states } })}
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
 * A tool group: its switch, its default state, what its loaded tools add to every model call, and
 * its tools with their states, or `unlisted` until a turn lists them. A state the instance changed
 * is marked, and resets to the group's own, `owner`'s default.
 */
function ToolGroupItem({
  title,
  group,
  unlisted,
  owner,
  disabled,
  onChange,
}: {
  title: string
  group: {
    on: boolean
    default_state: ToolState
    loaded_tokens: number
    tools: GroupTool[] | null
    instance_states: ToolGroupStates
  }
  unlisted: string
  owner: string
  disabled: boolean
  onChange: (states: ToolGroupStates) => void
}) {
  const id = useId()
  const states = group.instance_states
  const setTool = (name: string, state: ToolState | undefined) => {
    const others = Object.entries(states.tool_states ?? {}).filter(([tool]) => tool !== name)
    onChange({
      ...states,
      tool_states: Object.fromEntries(state === undefined ? others : [...others, [name, state]]),
    })
  }

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
        <ItemActions className="flex-wrap justify-end">
          {group.on && states.default_state != null && (
            <Changed
              reset={`Reset to ${owner} default`}
              disabled={disabled}
              onReset={() => onChange({ ...states, default_state: null })}
            />
          )}
          {group.on && (
            <StateChoice
              label="Default state"
              value={group.default_state}
              disabled={disabled}
              onChange={(state) => onChange({ ...states, default_state: state })}
            />
          )}
          <Field orientation="horizontal" className="w-auto">
            <Switch
              id={id}
              checked={group.on}
              disabled={disabled}
              onCheckedChange={(on) => onChange({ ...states, on })}
            />
            <FieldLabel htmlFor={id}>On</FieldLabel>
          </Field>
        </ItemActions>
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
                <ItemActions className="flex-wrap justify-end">
                  {tool.source === "instance" && (
                    <Changed
                      reset="Reset"
                      disabled={disabled}
                      onReset={() => setTool(tool.name, undefined)}
                    />
                  )}
                  <StateChoice
                    label="State"
                    value={tool.state}
                    disabled={disabled || !group.on}
                    onChange={(state) => setTool(tool.name, state)}
                  />
                </ItemActions>
              </Item>
            </li>
          ))}
        </ItemGroup>
      )}
    </li>
  )
}

/** The mark on a state the instance changed, and the button that resets it. */
function Changed({
  reset,
  disabled,
  onReset,
}: {
  reset: string
  disabled: boolean
  onReset: () => void
}) {
  return (
    <>
      <Badge variant="secondary">Changed</Badge>
      <Button size="xs" variant="ghost" disabled={disabled} onClick={onReset}>
        {reset}
      </Button>
    </>
  )
}

/** Loaded, searchable or off, as three toggles. */
function StateChoice({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string
  value: ToolState
  disabled: boolean
  onChange: (state: ToolState) => void
}) {
  return (
    <ToggleGroup
      aria-label={label}
      size="sm"
      variant="outline"
      value={[value]}
      disabled={disabled}
      onValueChange={([state]) => {
        const chosen = TOOL_STATES.find((each) => each === state)
        if (chosen !== undefined && chosen !== value) onChange(chosen)
      }}
    >
      {TOOL_STATES.map((state) => (
        <ToggleGroupItem key={state} value={state}>
          {STATES[state]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
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
