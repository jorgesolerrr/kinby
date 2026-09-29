import type { GateAction, InstanceClient, PermissionMode } from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { Failure, StaleAlert } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Item, ItemActions, ItemContent, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { lastChanged } from "@/lib/config-changes"
import { reason } from "@/lib/operation"
import {
  aboveCeiling,
  MODES,
  type OpenedPermissions,
  openPermissions,
  PERMISSIONS_FILE,
  type PermissionsDraft,
  savePermissions,
} from "@/lib/permissions"
import { LockIcon, XIcon } from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/** A tool's rule, where "mode" means it has none of its own and follows the mode. */
type Rule = GateAction | "mode"

const RULES: Record<Rule, string> = {
  mode: "Follow mode",
  allow: "allow",
  ask: "ask",
  deny: "deny",
}

function isRule(value: unknown): value is Rule {
  return typeof value === "string" && value in RULES
}

function isMode(value: unknown): value is PermissionMode {
  return MODES.some((mode) => mode === value)
}

/**
 * The ceiling, the default mode, each tool's rule, and the shell patterns. kinby's deny patterns
 * show locked, and a save sends only the instance's own.
 */
export function PermissionsSection({ client }: { client: Caller }) {
  const toolId = useId()
  const [opened, setOpened] = useState<OpenedPermissions>()
  const [draft, setDraft] = useState<PermissionsDraft>()
  // A tool set back to follow the mode keeps its row until the next read.
  const [tools, setTools] = useState<string[]>([])
  const [newTool, setNewTool] = useState("")
  const [lowered, setLowered] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [fields, setFields] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<string>()

  const show = useCallback((read: OpenedPermissions) => {
    setOpened(read)
    setDraft(read.draft)
    setTools(Object.keys(read.draft.tools))
    setLowered(false)
    setFields({})
  }, [])
  const load = useCallback(
    () =>
      openPermissions(client).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(reason(error)),
      ),
    [client, show],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (opened === undefined || draft === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure>{failure}</Failure>
    )
  }
  const change = (next: PermissionsDraft) => {
    setDraft(next)
    setFields({})
    if (notice === "saved") setNotice(undefined)
  }
  const setCeiling = (ceiling: PermissionMode) => {
    const lower = aboveCeiling(draft.mode, ceiling)
    setLowered(lower)
    change({ ...draft, ceiling, mode: lower ? ceiling : draft.mode })
  }
  const setRule = (tool: string, rule: Rule) => {
    const others = Object.fromEntries(Object.entries(draft.tools).filter(([name]) => name !== tool))
    change({ ...draft, tools: rule === "mode" ? others : { ...others, [tool]: rule } })
  }
  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      const saved = await savePermissions(client, draft, opened.hash)
      if (saved.state === "invalid") {
        setFields(saved.fields)
        return
      }
      if (saved.state === "saved") show(saved.opened)
      setNotice(saved.state)
    } catch (error) {
      setFailure(reason(error))
    } finally {
      setSaving(false)
    }
  }
  const tool = newTool.trim()

  return (
    <div className="flex flex-col gap-6">
      {notice === "stale" && <StaleAlert file={PERMISSIONS_FILE} onLoad={() => void load()} />}
      {failure !== undefined && <Failure>{failure}</Failure>}
      <FieldGroup>
        <FieldSet>
          <FieldLegend variant="label">Ceiling</FieldLegend>
          <ModeToggle value={draft.ceiling} onChange={setCeiling} />
          <FieldDescription>The widest mode any thread or routine may use.</FieldDescription>
        </FieldSet>
        <FieldSet>
          <FieldLegend variant="label">Default mode</FieldLegend>
          <ModeToggle
            value={draft.mode}
            ceiling={draft.ceiling}
            onChange={(mode) => {
              setLowered(false)
              change({ ...draft, mode })
            }}
          />
          <FieldDescription>
            {lowered
              ? `Lowered the default mode to ${draft.mode} to stay within the ceiling.`
              : "The mode a new thread starts in. Modes above the ceiling are off."}
          </FieldDescription>
        </FieldSet>
        <FieldSet>
          <FieldLegend variant="label">Tool rules</FieldLegend>
          <FieldDescription>A tool without a rule of its own follows the mode.</FieldDescription>
          {tools.length > 0 && (
            <ItemGroup>
              {tools.map((name) => (
                <li key={name}>
                  <Item size="xs" variant="outline">
                    <ItemContent>
                      <ItemTitle>
                        <code>{name}</code>
                      </ItemTitle>
                    </ItemContent>
                    <ItemActions>
                      <ToggleGroup
                        aria-label={name}
                        size="sm"
                        variant="outline"
                        value={[draft.tools[name] ?? "mode"]}
                        onValueChange={([rule]) => {
                          if (isRule(rule)) setRule(name, rule)
                        }}
                      >
                        {Object.entries(RULES).map(([rule, label]) => (
                          <ToggleGroupItem key={rule} value={rule}>
                            {label}
                          </ToggleGroupItem>
                        ))}
                      </ToggleGroup>
                    </ItemActions>
                  </Item>
                </li>
              ))}
            </ItemGroup>
          )}
          <Field>
            <FieldLabel htmlFor={toolId}>Tool</FieldLabel>
            <div className="flex gap-2">
              <Input
                id={toolId}
                placeholder="web_fetch"
                value={newTool}
                onChange={(event) => setNewTool(event.target.value)}
              />
              <Button
                variant="outline"
                disabled={tool === "" || tools.includes(tool)}
                onClick={() => {
                  setTools([...tools, tool])
                  setNewTool("")
                }}
              >
                Add rule
              </Button>
            </div>
          </Field>
        </FieldSet>
        <PatternList
          label="Always denied"
          noun="denied"
          description="kinby's own patterns always apply and cannot be removed."
          locked={opened.shipped}
          patterns={draft.deny}
          errors={draft.deny.map((_, index) => fields[`bash.deny.${index}`])}
          onChange={(deny) => change({ ...draft, deny })}
        />
        <PatternList
          label="Always ask"
          noun="asked"
          description="A command that matches asks for approval in every mode but read-only."
          locked={[]}
          patterns={draft.ask}
          errors={draft.ask.map((_, index) => fields[`bash.ask.${index}`])}
          onChange={(ask) => change({ ...draft, ask })}
        />
      </FieldGroup>
      <div className="flex items-center gap-3">
        <Button
          disabled={saving || JSON.stringify(draft) === JSON.stringify(opened.draft)}
          onClick={() => void save()}
        >
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">Saved. It applies at the next turn.</span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{lastChanged(opened.lastChange)}</p>
    </div>
  )
}

/** The four modes, with the ones above `ceiling` off. */
function ModeToggle({
  value,
  ceiling,
  onChange,
}: {
  value: PermissionMode
  ceiling?: PermissionMode
  onChange: (mode: PermissionMode) => void
}) {
  return (
    <ToggleGroup
      variant="outline"
      value={[value]}
      onValueChange={([mode]) => {
        if (isMode(mode)) onChange(mode)
      }}
    >
      {MODES.map((mode) => (
        <ToggleGroupItem
          key={mode}
          value={mode}
          disabled={ceiling !== undefined && aboveCeiling(mode, ceiling)}
        >
          {mode}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

/** Shell patterns: the locked ones first, then the instance's own, which can be removed or added. */
function PatternList({
  label,
  noun,
  description,
  locked,
  patterns,
  errors,
  onChange,
}: {
  label: string
  noun: string
  description: string
  locked: string[]
  patterns: string[]
  /** Why the instance refused each of `patterns`, by its index. */
  errors: (string | undefined)[]
  onChange: (patterns: string[]) => void
}) {
  const [value, setValue] = useState("")
  const known = locked.includes(value) || patterns.includes(value)
  return (
    <FieldSet>
      <FieldLegend variant="label">{label}</FieldLegend>
      <FieldDescription>{description}</FieldDescription>
      {locked.length + patterns.length > 0 && (
        <ItemGroup aria-label={label}>
          {locked.map((pattern) => (
            <li key={pattern}>
              <Item size="xs" variant="muted">
                <ItemContent>
                  <ItemTitle>
                    <code className="break-all">{pattern}</code>
                  </ItemTitle>
                </ItemContent>
                <ItemActions>
                  <Badge variant="secondary">
                    <LockIcon data-icon="inline-start" />
                    Shipped
                  </Badge>
                </ItemActions>
              </Item>
            </li>
          ))}
          {patterns.map((pattern) => (
            <li key={pattern}>
              <Item size="xs" variant="outline">
                <ItemContent>
                  <ItemTitle>
                    <code className="break-all">{pattern}</code>
                  </ItemTitle>
                </ItemContent>
                <ItemActions>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Remove ${pattern}`}
                    onClick={() => onChange(patterns.filter((other) => other !== pattern))}
                  >
                    <XIcon />
                  </Button>
                </ItemActions>
              </Item>
            </li>
          ))}
        </ItemGroup>
      )}
      {patterns.map(
        (pattern, index) =>
          errors[index] !== undefined && (
            <FieldError key={pattern}>{`${pattern}: ${errors[index]}`}</FieldError>
          ),
      )}
      <div className="flex gap-2">
        <Input
          aria-label={`New ${noun} pattern`}
          placeholder="A regex, such as ^npm publish"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <Button
          variant="outline"
          aria-label={`Add ${noun} pattern`}
          disabled={value === "" || known}
          onClick={() => {
            onChange([...patterns, value])
            setValue("")
          }}
        >
          Add
        </Button>
      </div>
    </FieldSet>
  )
}
