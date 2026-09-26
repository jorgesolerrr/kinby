// PROTOTYPE, throwaway. The config sections, shared by the three variants. The variants differ in
// how they arrange and reach these sections, not in what a section shows.
import { type ComponentType, useEffect, useState } from "react"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import {
  BoxIcon,
  CheckIcon,
  CpuIcon,
  FileTextIcon,
  KeyRoundIcon,
  type LucideProps,
  PlayIcon,
  RepeatIcon,
  ShieldIcon,
  SparklesIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react"

import type { Config, Operation, WriteResult } from "./flow"
import { type ConfigFile, FILE_LABEL, INSTANCE } from "./stub"

// ---------- file editor ----------

export function FileEditor({ config, file }: { config: Config; file: ConfigFile }) {
  const [draft, setDraft] = useState("")
  const [hash, setHash] = useState("")
  const [result, setResult] = useState<WriteResult | null>(null)

  useEffect(() => {
    const { content, hash } = config.read(file)
    setDraft(content)
    setHash(hash)
    setResult(null)
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- prototype: read once per file
  }, [file])

  const save = () => {
    const r = config.write(file, draft, hash)
    setResult(r)
    if (r.kind === "ok") setHash(r.hash)
  }
  const dirty = draft !== config.files[file].content

  return (
    <div className="flex flex-col gap-3">
      {file === "kinby.toml" && <ModelsSummary config={config} />}
      <Textarea
        className="min-h-72"
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value)
          setResult(null)
        }}
        aria-invalid={result?.kind === "problems"}
      />
      <div className="flex items-center gap-2">
        <Button onClick={save} disabled={!dirty}>
          Save
        </Button>
        <Button
          variant="ghost"
          disabled={!dirty}
          onClick={() => {
            const { content, hash } = config.read(file)
            setDraft(content)
            setHash(hash)
            setResult(null)
          }}
        >
          Discard
        </Button>
        <span className="text-xs text-muted-foreground">
          {file} · {hash}
        </span>
      </div>
      {result?.kind === "ok" && (
        <Alert>
          <CheckIcon />
          <AlertTitle>Saved</AlertTitle>
          <AlertDescription>
            {result.restart_required.length > 0
              ? "Applies after the instance is recreated. See the notice at the top."
              : `Applies ${result.applies}.`}
          </AlertDescription>
        </Alert>
      )}
      {result?.kind === "problems" && (
        <Alert variant="destructive">
          <XIcon />
          <AlertTitle>Not saved: the instance rejected this file</AlertTitle>
          <AlertDescription>
            <ul>
              {result.problems.map((p) => (
                <li key={p.message}>
                  {p.line !== undefined && <strong>Line {p.line}: </strong>}
                  {p.message}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      {result?.kind === "conflict" && (
        <Alert variant="destructive">
          <XIcon />
          <AlertTitle>{file} changed since you opened it</AlertTitle>
          <AlertDescription>
            Someone else wrote it, most likely the agent or the routine failure policy. Load their
            version, or keep yours and save over it.
          </AlertDescription>
          <AlertAction>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setDraft(result.current)
                  setHash(result.hash)
                  setResult(null)
                }}
              >
                Load theirs
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  setHash(result.hash)
                  setResult(null)
                }}
              >
                Keep mine
              </Button>
            </div>
          </AlertAction>
        </Alert>
      )}
    </div>
  )
}

function ModelsSummary({ config }: { config: Config }) {
  const models = config.models()
  return (
    <ItemGroup className="grid grid-cols-2">
      <Item variant="outline" size="sm">
        <ItemContent>
          <ItemDescription>Main model</ItemDescription>
          <ItemTitle>{models.main}</ItemTitle>
        </ItemContent>
      </Item>
      <Item variant="outline" size="sm">
        <ItemContent>
          <ItemDescription>Recap model</ItemDescription>
          <ItemTitle>{models.recap}</ItemTitle>
        </ItemContent>
      </Item>
    </ItemGroup>
  )
}

// ---------- routines ----------

export function RoutinesSection({ config }: { config: Config }) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const [result, setResult] = useState<WriteResult | null>(null)

  useEffect(() => {
    config.log("routine.list", {}, { routines: config.routines.length }, false)
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- prototype: list once
  }, [])

  return (
    <div className="flex flex-col gap-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Routine</TableHead>
            <TableHead>Trigger</TableHead>
            <TableHead>Next</TableHead>
            <TableHead>Last</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>On</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {config.routines.map((r) => (
            <TableRow key={r.name}>
              <TableCell>
                <div className="flex flex-col">
                  <span className="font-medium">{r.name}</span>
                  <span className="text-xs text-muted-foreground">{r.description}</span>
                  {!r.enabled && r.failure_count >= 10 && (
                    <Badge variant="destructive">turned off after {r.failure_count} failures</Badge>
                  )}
                </div>
              </TableCell>
              <TableCell>
                {r.schedule ? <code>{r.schedule}</code> : r.signal ? "signal" : "manual"}
              </TableCell>
              <TableCell>{r.next_run ?? "—"}</TableCell>
              <TableCell>{r.last_run ?? "—"}</TableCell>
              <TableCell>
                <Badge variant="outline">{r.mode}</Badge>
              </TableCell>
              <TableCell>
                <Switch
                  checked={r.enabled}
                  onCheckedChange={(checked) => config.setRoutineEnabled(r.name, checked)}
                  aria-label={`Turn ${r.name} on or off`}
                />
              </TableCell>
              <TableCell>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => config.runRoutine(r.name)}>
                    <PlayIcon data-icon="inline-start" />
                    Run now
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditing(r.name)
                      setDraft(r.content)
                      setResult(null)
                      config.log(
                        "routine.read",
                        { name: r.name },
                        { content: "…", hash: "sha256:…" },
                        true,
                      )
                    }}
                  >
                    Edit
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {editing !== null && (
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium">routines/{editing}/ROUTINE.md</span>
          <Textarea className="min-h-48" value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="flex gap-2">
            <Button onClick={() => setResult(config.writeRoutine(editing, draft))}>Save</Button>
            <Button variant="ghost" onClick={() => setEditing(null)}>
              Close
            </Button>
          </div>
          {result?.kind === "problems" && (
            <Alert variant="destructive">
              <XIcon />
              <AlertTitle>Not saved</AlertTitle>
              <AlertDescription>{result.problems.map((p) => p.message).join(" ")}</AlertDescription>
            </Alert>
          )}
          {result?.kind === "ok" && (
            <Alert>
              <CheckIcon />
              <AlertTitle>Saved</AlertTitle>
              <AlertDescription>Applies {result.applies}.</AlertDescription>
            </Alert>
          )}
        </div>
      )}
    </div>
  )
}

// ---------- skills ----------

export function SkillsSection({ config }: { config: Config }) {
  useEffect(() => {
    config.listSkills()
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- prototype: list once
  }, [])
  const names = [...new Set(config.skills.map((s) => s.name))]
  return (
    <ItemGroup>
      {names.map((name) => {
        const [winner, ...shadowed] = config.skills.filter((s) => s.name === name)
        return (
          <div key={name} className="flex flex-col gap-1">
            <Item variant="outline">
              <ItemContent>
                <ItemTitle>
                  {winner.name}{" "}
                  <Badge variant={winner.tier === "instance" ? "default" : "secondary"}>
                    {winner.tier}
                  </Badge>
                </ItemTitle>
                <ItemDescription>{winner.description}</ItemDescription>
              </ItemContent>
              <ItemActions>
                {winner.tier === "instance" && shadowed.length > 0 ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => config.removeCustomization(name)}
                  >
                    Remove customization
                  </Button>
                ) : winner.tier === "instance" ? (
                  <Button size="sm" variant="outline">
                    Edit
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" onClick={() => config.customizeSkill(winner)}>
                    Customize
                  </Button>
                )}
              </ItemActions>
            </Item>
            {shadowed.map((s) => (
              <Item key={s.tier} size="xs" variant="muted" className="ml-6">
                <ItemContent>
                  <ItemDescription>
                    {s.tier} version, hidden by the {winner.tier} one
                  </ItemDescription>
                </ItemContent>
              </Item>
            ))}
          </div>
        )
      })}
    </ItemGroup>
  )
}

// ---------- tools ----------

export function ToolsSection({ config }: { config: Config }) {
  useEffect(() => {
    config.listTools()
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- prototype: list once
  }, [])
  return (
    <div className="flex flex-col gap-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Tool</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Writes</TableHead>
            <TableHead>Rule that applies</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {config.tools.map((t) => (
            <TableRow key={t.name}>
              <TableCell>
                <code>{t.name}</code>
              </TableCell>
              <TableCell>{t.source}</TableCell>
              <TableCell>{t.writes ? "yes" : "no"}</TableCell>
              <TableCell>{config.permissionRule(t.name)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-xs text-muted-foreground">
        Tools are read-only here. Change a tool's rule in Permissions.
      </p>
    </div>
  )
}

// ---------- secrets and login ----------

export function SecretsSection({ config }: { config: Config }) {
  const [values, setValues] = useState<Record<string, string>>({})
  useEffect(() => {
    config.status()
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- prototype: status once
  }, [])
  return (
    <div className="flex flex-col gap-6">
      <FieldGroup>
        {config.secrets.map((s) => (
          <Field key={s.name} orientation="horizontal">
            <FieldLabel htmlFor={s.name} className="w-48">
              {s.name}
            </FieldLabel>
            <Input
              id={s.name}
              type="password"
              placeholder={
                s.is_set
                  ? "set · type to replace"
                  : s.required
                    ? "required, not set"
                    : "optional, not set"
              }
              value={values[s.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [s.name]: e.target.value }))}
            />
            <Button
              variant="outline"
              disabled={!values[s.name]}
              onClick={() => {
                config.setSecret(s.name)
                setValues((v) => ({ ...v, [s.name]: "" }))
              }}
            >
              Set
            </Button>
          </Field>
        ))}
        <FieldDescription>
          Values are write-only. A new value applies after the instance is recreated.
        </FieldDescription>
      </FieldGroup>
      <ItemGroup>
        {config.logins.map((l) => (
          <Item key={l.name} variant="outline">
            <ItemContent>
              <ItemTitle>
                {l.name} subscription
                <Badge variant={l.status === "complete" ? "secondary" : "destructive"}>
                  {l.status === "complete" ? "signed in" : "sign-in expired"}
                </Badge>
              </ItemTitle>
              {config.operation?.kind === `login ${l.name}` &&
                config.operation.state === "running" && (
                  <ItemDescription>Open {config.operation.steps[0].detail}</ItemDescription>
                )}
            </ItemContent>
            <ItemActions>
              {config.operation?.kind === `login ${l.name}` &&
              config.operation.state === "running" ? (
                <Button size="sm" onClick={() => config.finishLogin(l.name)}>
                  (stub) I signed in
                </Button>
              ) : (
                <Button size="sm" variant="outline" onClick={() => config.login(l.name)}>
                  Sign in again
                </Button>
              )}
            </ItemActions>
          </Item>
        ))}
      </ItemGroup>
    </div>
  )
}

// ---------- package and version ----------

export function PackageSection({ config }: { config: Config }) {
  const [ref, setRef] = useState("")
  const op = config.operation?.kind === "instance.update" ? config.operation : null
  const behind = config.revision !== INSTANCE.hubRevision
  return (
    <div className="flex flex-col gap-4">
      <ItemGroup className="grid grid-cols-3">
        <Item variant="outline" size="sm">
          <ItemContent>
            <ItemDescription>Package</ItemDescription>
            <ItemTitle>{INSTANCE.package.id}</ItemTitle>
          </ItemContent>
        </Item>
        <Item variant="outline" size="sm">
          <ItemContent>
            <ItemDescription>Started from template</ItemDescription>
            <ItemTitle>{INSTANCE.package.initialized_version}</ItemTitle>
          </ItemContent>
        </Item>
        <Item variant="outline" size="sm">
          <ItemContent>
            <ItemDescription>Installed</ItemDescription>
            <ItemTitle>{INSTANCE.package.installed_version}</ItemTitle>
          </ItemContent>
        </Item>
      </ItemGroup>
      <Alert>
        <BoxIcon />
        <AlertTitle>The package template moved on</AlertTitle>
        <AlertDescription>
          This instance started from template {INSTANCE.package.initialized_version};{" "}
          {INSTANCE.package.installed_version} is installed. Your prompts, permissions, and routines
          stay as you left them.
        </AlertDescription>
      </Alert>
      <FieldGroup>
        <Field>
          <FieldLabel>kinby version</FieldLabel>
          <FieldDescription>
            This instance runs <code>{config.revision}</code>. The hub runs{" "}
            <code>{INSTANCE.hubRevision}</code>.
          </FieldDescription>
        </Field>
        <div className="flex gap-2">
          <Button
            disabled={!behind || op?.state === "running"}
            onClick={() => config.update(INSTANCE.hubRevision)}
          >
            Update to the hub's version
          </Button>
          <Input
            placeholder="or a tag, branch, or commit"
            value={ref}
            onChange={(e) => setRef(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={!ref || op?.state === "running"}
            onClick={() => config.update(ref)}
          >
            Update
          </Button>
        </div>
      </FieldGroup>
      {op && <OperationSteps op={op} />}
      {op?.state === "failed" && (
        <Alert variant="destructive">
          <XIcon />
          <AlertTitle>Update stopped before touching the instance</AlertTitle>
          <AlertDescription>
            The new version can't run this instance's configuration. It is still running{" "}
            {config.revision}.
            <ul>
              {config.preflightProblems.map((p) => (
                <li key={p.message}>{p.message}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}

export function OperationSteps({ op }: { op: Operation }) {
  return (
    <ol className="flex flex-col gap-1 text-sm">
      {op.steps.map((s) => (
        <li key={s.name} className="flex items-center gap-2">
          {s.state === "running" ? (
            <Spinner />
          ) : s.state === "done" ? (
            <CheckIcon className="text-muted-foreground" />
          ) : s.state === "failed" ? (
            <XIcon className="text-destructive" />
          ) : (
            <span className="size-4" />
          )}
          <span className={s.state === "waiting" ? "text-muted-foreground" : ""}>{s.name}</span>
          {s.detail && <span className="text-xs text-muted-foreground">{s.detail}</span>}
        </li>
      ))}
    </ol>
  )
}

// ---------- recreate notice ----------

export function RecreateNotice({ config }: { config: Config }) {
  const op = config.operation?.kind === "instance.recreate" ? config.operation : null
  if (config.pending.length === 0 && op?.state !== "running") return null
  return (
    <Alert>
      <RepeatIcon />
      <AlertTitle>Recreate to apply</AlertTitle>
      <AlertDescription>
        {op?.state === "running" ? (
          <OperationSteps op={op} />
        ) : (
          <>
            Waiting to apply: {config.pending.join(", ")}. Recreating finishes the running turn
            first.
          </>
        )}
      </AlertDescription>
      {op?.state !== "running" && (
        <AlertAction>
          <Button size="sm" onClick={config.recreate}>
            Recreate
          </Button>
        </AlertAction>
      )}
    </Alert>
  )
}

// ---------- sections ----------

export type Section = {
  key: string
  label: string
  hint: string
  icon: ComponentType<LucideProps>
  render: (config: Config) => React.ReactNode
  attention?: (config: Config) => string | null
}

const fileSection = (
  file: ConfigFile,
  key: string,
  hint: string,
  icon: ComponentType<LucideProps>,
): Section => ({
  key,
  label: FILE_LABEL[file],
  hint,
  icon,
  render: (config) => <FileEditor key={file} config={config} file={file} />,
})

export const SECTIONS: Section[] = [
  fileSection("SYSTEM.md", "behavior", "SYSTEM.md, the instructions to the model", FileTextIcon),
  fileSection("RECAP.md", "recap", "RECAP.md, how threads are summarized", FileTextIcon),
  fileSection("permissions.toml", "permissions", "default mode, ceiling, tool rules", ShieldIcon),
  fileSection("kinby.toml", "manifest", "models, budgets, timezone", CpuIcon),
  {
    key: "routines",
    label: "Routines",
    hint: "schedules, signals, next firing",
    icon: RepeatIcon,
    render: (config) => <RoutinesSection config={config} />,
    attention: (config) => {
      const off = config.routines.filter((r) => !r.enabled && r.failure_count >= 10).length
      return off > 0 ? `${off} turned off` : null
    },
  },
  {
    key: "skills",
    label: "Skills",
    hint: "instance, package, workspace",
    icon: SparklesIcon,
    render: (config) => <SkillsSection config={config} />,
  },
  {
    key: "tools",
    label: "Tools",
    hint: "read-only, with the rule that applies",
    icon: WrenchIcon,
    render: (config) => <ToolsSection config={config} />,
  },
  {
    key: "secrets",
    label: "Secrets and login",
    hint: "write-only values, subscription sign-in",
    icon: KeyRoundIcon,
    render: (config) => <SecretsSection config={config} />,
    attention: (config) => {
      const expired = config.logins.filter((l) => l.status !== "complete").length
      return expired > 0 ? `${expired} sign-in expired` : null
    },
  },
  {
    ...fileSection("package.yaml", "package-config", "check commands, skill picks", BoxIcon),
  },
  {
    key: "version",
    label: "Package and version",
    hint: "template, installed, update core",
    icon: BoxIcon,
    render: (config) => <PackageSection config={config} />,
    attention: (config) => (config.revision !== INSTANCE.hubRevision ? "behind the hub" : null),
  },
]

// ---------- state panel ----------

export function StatePanel({ config }: { config: Config }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="fixed right-4 bottom-16 z-40 flex w-96 flex-col gap-2 rounded-lg border bg-background p-3 text-xs shadow-lg">
      <div className="flex items-center justify-between">
        <span className="font-medium">Prototype state</span>
        <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      </div>
      {open && (
        <>
          <div className="flex flex-wrap gap-1">
            <Button size="xs" variant="outline" onClick={() => config.agentEdit("SYSTEM.md")}>
              Agent edits SYSTEM.md
            </Button>
            <Button size="xs" variant="outline" onClick={() => config.agentEdit("kinby.toml")}>
              Agent edits kinby.toml
            </Button>
            <Button
              size="xs"
              variant={config.preflightFails ? "destructive" : "outline"}
              onClick={() => config.setPreflightFails(!config.preflightFails)}
            >
              Preflight fails: {config.preflightFails ? "yes" : "no"}
            </Button>
          </div>
          <p className="text-muted-foreground">
            Try: change <code>listen</code> or <code>main</code> in the manifest, save package
            config, set a secret. Red calls are missing from the v1 contract.
          </p>
          <ol className="flex max-h-72 flex-col gap-1 overflow-y-auto font-mono">
            {config.calls.map((c) => (
              <li key={c.id} className={c.missing ? "text-destructive" : ""}>
                {c.method} {c.params} → {c.result}
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}
