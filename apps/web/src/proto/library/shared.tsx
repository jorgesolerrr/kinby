// PROTOTYPE: leaf pieces both Library variants draw: who uses an entry, the skill and MCP server
// editors, the add forms and a factory's body. Layout stays in each variant. Throwaway.
import { CircleAlertIcon, GitBranchIcon, RefreshCwIcon } from "lucide-react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "cn"

import { instanceById } from "../shell/mock"
import type { Go } from "../shell/screens"
import {
  type Entry,
  type Factory,
  FACTORY_YAML,
  GIT_PREVIEW,
  PICK_STATES,
  type PickState,
  SKILL_MD,
  SOURCES,
} from "./mock"

/** Where an entry came from, as one muted line. */
export function origin(e: Entry) {
  if (e.git) return `${e.git.url}/${e.git.path} at ${e.git.commit}`
  return `${SOURCES[e.source]}${e.edited ? " · edited here" : ""}`
}

/** "Used by Kin, Coder" or "Not picked yet". */
export function usedLine(e: Entry) {
  if (e.usedBy.length === 0) return "Not picked yet"
  const names = e.usedBy.map((u) => instanceById(u.instanceId).name).join(", ")
  return e.kind === "factory" ? `Held by ${names}` : `Used by ${names}`
}

/** The instances an entry is in, as a small avatar stack. */
export function UsedStack({ entry }: { entry: Entry }) {
  if (entry.usedBy.length === 0) return null
  return (
    <span className="flex -space-x-1.5" aria-label={usedLine(entry)}>
      {entry.usedBy.map((u) => {
        const i = instanceById(u.instanceId)
        return <InstanceAvatar key={i.id} avatar={i.avatar} name={i.name} size="sm" />
      })}
    </span>
  )
}

const stateBadge = (s: PickState) =>
  s === "behind" ? (
    <Badge variant="attention">Behind</Badge>
  ) : s === "edited" ? (
    <Badge variant="outline">Edited there</Badge>
  ) : null

/** Each instance that picked the entry, how its copy stands, and a way to go to it. */
export function UsedBy({ entry, go }: { entry: Entry; go: Go }) {
  if (entry.usedBy.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No instance has picked {entry.name}. Pick it from an instance's Config → Skills
        {entry.kind === "mcp_server" ? " or Tools" : ""}.
      </p>
    )
  }
  return (
    <ItemGroup className="gap-0 divide-y rounded-xl border [&>[data-slot=item]]:rounded-none">
      {entry.usedBy.map((u) => {
        const i = instanceById(u.instanceId)
        return (
          <Item key={i.id} size="xs">
            <ItemMedia>
              <InstanceAvatar avatar={i.avatar} name={i.name} size="sm" />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>
                {i.name}
                {stateBadge(u.state)}
              </ItemTitle>
              <ItemDescription>
                {PICK_STATES[u.state]}
                {u.state === "behind" && " · the next instance update brings it up"}
              </ItemDescription>
            </ItemContent>
            <ItemActions>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => go({ kind: "instance", instanceId: i.id, tab: "config" })}
              >
                Open
              </Button>
            </ItemActions>
          </Item>
        )
      })}
    </ItemGroup>
  )
}

/** The newer-version notice and check a git entry carries. */
export function GitVersion({ entry }: { entry: Entry }) {
  const [checked, setChecked] = useState(false)
  if (entry.source !== "git") return null
  return entry.newer ? (
    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-ember/10 px-3 py-2 text-sm">
      <span className="size-2 shrink-0 rounded-full bg-ember" />
      <span className="flex-1">A newer commit, 9c04b17, is on the repository.</span>
      <Button size="sm" variant="outline">
        Move to 9c04b17
      </Button>
    </div>
  ) : (
    <div className="flex items-center gap-3 text-sm text-muted-foreground">
      <GitBranchIcon className="size-4" />
      <span className="flex-1">{origin(entry)}</span>
      <Button size="sm" variant="ghost" onClick={() => setChecked(true)}>
        <RefreshCwIcon data-icon="inline-start" />
        {checked ? "No newer version" : "Check for a newer version"}
      </Button>
    </div>
  )
}

export function SkillEditor({ entry, rows = 14 }: { entry: Entry; rows?: number }) {
  return (
    <Field>
      <FieldLabel className="font-mono">SKILL.md</FieldLabel>
      <Textarea
        className="font-mono text-sm"
        style={{ minHeight: `${rows * 1.4}rem` }}
        defaultValue={entry.name === "trip-planner" ? SKILL_MD : skillMd(entry)}
      />
      {entry.source === "shipped" && (
        <FieldDescription>
          kinby ships this skill. Saving keeps your edit; a kinby update won't overwrite it.
        </FieldDescription>
      )}
    </Field>
  )
}

const skillMd = (e: Entry) =>
  `---\nname: ${e.name}\ndescription: ${e.description}\n---\n\n# ${e.name}\n\n…`

export function McpFields({ entry }: { entry?: Entry }) {
  const remote = entry === undefined || entry.description.includes("URL")
  const [transport, setTransport] = useState(remote ? "url" : "uvx")
  return (
    <FieldGroup>
      <FieldSet>
        <FieldLegend variant="label">Transport</FieldLegend>
        <ToggleGroup
          variant="outline"
          value={[transport]}
          onValueChange={([t]) => t && setTransport(t)}
        >
          <ToggleGroupItem value="url">URL</ToggleGroupItem>
          <ToggleGroupItem value="uvx">uvx package</ToggleGroupItem>
        </ToggleGroup>
      </FieldSet>
      {transport === "url" ? (
        <>
          <Field>
            <FieldLabel>URL</FieldLabel>
            <Input
              className="font-mono"
              defaultValue={entry ? `https://mcp.${entry.name}.com/mcp` : ""}
              placeholder="https://"
            />
            <FieldDescription>A remote server, reached over HTTP.</FieldDescription>
          </Field>
          <Field>
            <FieldLabel>Headers</FieldLabel>
            <Textarea
              className="min-h-16 font-mono text-sm"
              defaultValue={entry ? "Authorization: GITHUB_TOKEN" : ""}
            />
            <FieldDescription>
              One a line, as Name: VARIABLE. Each value is that variable's, from the instance's
              .env.
            </FieldDescription>
          </Field>
        </>
      ) : (
        <>
          <Field>
            <FieldLabel>Package</FieldLabel>
            <Input className="font-mono" defaultValue={entry ? `${entry.name}-mcp` : ""} />
          </Field>
          <Field>
            <FieldLabel>Variables</FieldLabel>
            <Textarea
              className="min-h-16 font-mono text-sm"
              defaultValue={entry ? "NOTION_TOKEN" : ""}
            />
            <FieldDescription>
              The variables it gets from the instance's .env, one a line. It gets no others.
            </FieldDescription>
          </Field>
        </>
      )}
      <Field>
        <FieldLabel>Tools</FieldLabel>
        <Textarea className="min-h-16 font-mono text-sm" placeholder="Leave empty for all" />
        <FieldDescription>The tools a turn is offered, one a line.</FieldDescription>
      </Field>
    </FieldGroup>
  )
}

/** Write a skill: a name and its SKILL.md. */
export function NewSkill({ onDone }: { onDone: () => void }) {
  return (
    <FieldGroup>
      <Field>
        <FieldLabel>Name</FieldLabel>
        <Input className="font-mono" placeholder="weekly-review" autoFocus />
        <FieldDescription>Lowercase letters, digits and hyphens.</FieldDescription>
      </Field>
      <Field>
        <FieldLabel className="font-mono">SKILL.md</FieldLabel>
        <Textarea
          className="min-h-48 font-mono text-sm"
          defaultValue={"---\nname: \ndescription: What it does. Use when …\n---\n\n"}
        />
      </Field>
      <div className="flex gap-2">
        <Button size="sm" onClick={onDone}>
          Create
        </Button>
        <Button size="sm" variant="outline" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </FieldGroup>
  )
}

/** Add from git: an address, then the skills the repository holds, to tick. */
export function AddFromGit({ onDone }: { onDone: () => void }) {
  const [shown, setShown] = useState(false)
  return (
    <FieldGroup>
      <Field>
        <FieldLabel>Repository</FieldLabel>
        <div className="flex gap-2">
          <Input
            className="font-mono"
            placeholder="owner/repo, or a git URL"
            defaultValue="mattpocock/skills"
          />
          <Button variant="outline" onClick={() => setShown(true)}>
            Look
          </Button>
        </div>
        <FieldDescription>kinby reads the repository and changes nothing yet.</FieldDescription>
      </Field>
      {shown && (
        <FieldSet>
          <FieldLegend variant="label">
            {GIT_PREVIEW.url} at {GIT_PREVIEW.commit}
          </FieldLegend>
          <ItemGroup className="gap-0 divide-y rounded-xl border [&>[data-slot=item]]:rounded-none">
            {GIT_PREVIEW.skills.map((s) => (
              <Item
                key={s.name}
                size="xs"
                className={cn(s.present && "opacity-60")}
                render={<label />}
              >
                <ItemMedia>
                  <Checkbox defaultChecked={!s.present} disabled={s.present} />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle className="font-mono">{s.name}</ItemTitle>
                  <ItemDescription>{s.description}</ItemDescription>
                </ItemContent>
              </Item>
            ))}
          </ItemGroup>
        </FieldSet>
      )}
      <div className="flex gap-2">
        <Button size="sm" disabled={!shown} onClick={onDone}>
          Add 3 skills
        </Button>
        <Button size="sm" variant="outline" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </FieldGroup>
  )
}

export function NewMcpServer({ onDone }: { onDone: () => void }) {
  return (
    <FieldGroup>
      <Field>
        <FieldLabel>Name</FieldLabel>
        <Input className="font-mono" placeholder="linear" autoFocus />
        <FieldDescription>
          Lowercase letters, digits and hyphens. Its tools are named after it.
        </FieldDescription>
      </Field>
      <McpFields />
      <div className="flex gap-2">
        <Button size="sm" onClick={onDone}>
          Add
        </Button>
        <Button size="sm" variant="outline" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </FieldGroup>
  )
}

const RUN_BADGE = {
  "needs-human": <Badge variant="attention">Needs you</Badge>,
  running: <Badge variant="success">Running</Badge>,
  queued: <Badge variant="outline">Queued</Badge>,
  parked: <Badge variant="outline">Parked</Badge>,
  done: <Badge variant="secondary">Done</Badge>,
}

/** A factory's instances, each with its runs, the ones needing a human first. */
export function FactoryInstances({ factory, go }: { factory: Factory; go: Go }) {
  return (
    <div className="flex flex-col gap-3">
      {factory.usedBy.map((u) => {
        const i = instanceById(u.instanceId)
        return (
          <ItemGroup
            key={i.id}
            className="gap-0 divide-y rounded-xl border [&>[data-slot=item]]:rounded-none"
          >
            <Item size="xs" className="bg-muted/40">
              <ItemMedia>
                <InstanceAvatar avatar={i.avatar} name={i.name} size="sm" />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>
                  {i.name}
                  <Badge variant="secondary">{factory.roles[0].name}</Badge>
                </ItemTitle>
                <ItemDescription>1 run needs you · 1 running</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => go({ kind: "instance", instanceId: i.id, tab: "overview" })}
                >
                  Open
                </Button>
              </ItemActions>
            </Item>
            {factory.runs.map((r) => (
              <Item key={r.id} size="xs">
                <ItemMedia variant="icon">
                  {r.status === "needs-human" ? (
                    <CircleAlertIcon className="text-ember" />
                  ) : (
                    <GitBranchIcon />
                  )}
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>
                    {r.title}
                    {RUN_BADGE[r.status]}
                  </ItemTitle>
                  <ItemDescription>
                    at {r.step} · {r.when}
                  </ItemDescription>
                </ItemContent>
                {r.status === "needs-human" && (
                  <ItemActions>
                    <Button size="sm" variant="outline">
                      Answer
                    </Button>
                  </ItemActions>
                )}
              </Item>
            ))}
          </ItemGroup>
        )
      })}
    </div>
  )
}

/** What a factory is made of: roles, steps and files, read-only. */
export function FactoryMakeup({ factory }: { factory: Factory }) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      {factory.roles.map((r) => (
        <dl key={r.name} className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5">
          <dt className="text-muted-foreground">Role</dt>
          <dd className="font-mono">
            {r.name} <span className="text-muted-foreground">· image {r.image}</span>
          </dd>
          <dt className="text-muted-foreground">Suggested picks</dt>
          <dd className="flex flex-wrap gap-1">
            {r.picks.map((p) => (
              <Badge key={p} variant="outline" className="font-mono">
                {p}
              </Badge>
            ))}
          </dd>
          <dt className="text-muted-foreground">Asks for</dt>
          <dd>{r.fields.join(", ")}</dd>
        </dl>
      ))}
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5">
        <dt className="text-muted-foreground">Steps</dt>
        <dd className="font-mono">{factory.steps.join(" → ")}</dd>
      </dl>
      <Field>
        <FieldLabel className="font-mono">factory.yaml</FieldLabel>
        <Textarea className="min-h-40 font-mono text-sm" readOnly defaultValue={FACTORY_YAML} />
        <FieldDescription>
          Also: {factory.files.join(", ")}. Ask an instance with the write-factory skill to change
          it.
        </FieldDescription>
      </Field>
    </div>
  )
}
