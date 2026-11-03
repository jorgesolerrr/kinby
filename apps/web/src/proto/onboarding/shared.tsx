// PROTOTYPE: the pieces both New instance variants arrange their own way. Throwaway.
import type { AvatarColor, AvatarShape } from "@kinby/contract"
import {
  CheckIcon,
  ChevronDownIcon,
  CircleIcon,
  CopyIcon,
  ExternalLinkIcon,
  KeyRoundIcon,
  PlayIcon,
} from "lucide-react"
import type * as React from "react"
import { useEffect, useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "cn"

import {
  CREATE_STEPS,
  IMAGE_STEPS,
  LOGIN,
  type MockField,
  OFFERED_SKILLS,
  ROLES,
  type Role,
  fieldsFor,
} from "./mock"

// ── State ──────────────────────────────────────────────────────────────────────────────────────

export interface Draft {
  role: Role
  name: string
  shape: AvatarShape
  color: AvatarColor
  skills: string[]
  prompt: string
  values: Record<string, string>
  /** A secret filled from a hub secret, by field name. */
  linked: Record<string, string>
}

const initial = (role: Role): Draft => ({
  role,
  name: role.proposedName,
  shape: role.avatar.shape,
  color: role.avatar.color,
  skills: role.picks,
  prompt: role.prompt,
  values: { model: "claude-sonnet-5-5" },
  linked: {},
})

export function useDraft() {
  const [draft, setDraft] = useState<Draft>(() => initial(ROLES[0]))
  const pickRole = (role: Role) =>
    setDraft((d) => ({
      ...initial(role),
      // A name the user typed stays; the proposed one follows the role.
      name: d.name === d.role.proposedName ? role.proposedName : d.name,
    }))
  const fields = fieldsFor(draft.role)
  const missing = fields.filter(
    (f) =>
      f.required &&
      f.kind !== "login" &&
      (draft.values[f.name] ?? "").trim() === "" &&
      draft.linked[f.name] === undefined,
  )
  return { draft, setDraft, pickRole, fields, missing }
}

/** The image prepares itself as the page opens; nothing waits on a click. */
export function useImage() {
  const [done, setDone] = useState(0)
  useEffect(() => {
    if (done >= IMAGE_STEPS.length) return
    const t = window.setTimeout(() => setDone((n) => n + 1), 900)
    return () => window.clearTimeout(t)
  }, [done])
  return { ready: done >= IMAGE_STEPS.length, done }
}

export function ImageStatus({ image }: { image: ReturnType<typeof useImage> }) {
  return image.ready ? (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <CheckIcon className="size-3.5 text-kin" />
      Image ready · kinby 3f2a1c
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <Spinner className="size-3.5" />
      Preparing the image · {IMAGE_STEPS[image.done]}
    </span>
  )
}

// ── Layout bits ────────────────────────────────────────────────────────────────────────────────

export function Section({
  title,
  lead,
  aside,
  children,
}: {
  title: string
  lead?: React.ReactNode
  aside?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-end gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h2 className="font-heading text-base font-semibold">{title}</h2>
          {lead && <p className="text-sm text-muted-foreground">{lead}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  )
}

/** The Rows grammar: one bordered list, rows divided, no cards inside. */
export function Rows({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <ItemGroup
      className={cn(
        "gap-0 divide-y rounded-xl border [&>[data-slot=item]]:rounded-none",
        className,
      )}
    >
      {children}
    </ItemGroup>
  )
}

// ── Role ───────────────────────────────────────────────────────────────────────────────────────

export function RoleRows({ draft, onPick }: { draft: Draft; onPick: (r: Role) => void }) {
  return (
    <Rows>
      {ROLES.map((r) => {
        const on = draft.role.id === r.id
        return (
          <Item
            key={r.id}
            size="sm"
            aria-pressed={on}
            className={cn("cursor-pointer text-left hover:bg-muted/50", on && "bg-muted/60")}
            render={<button type="button" onClick={() => onPick(r)} />}
          >
            <ItemContent>
              <ItemTitle>
                {r.factory === undefined ? (
                  "None"
                ) : (
                  <>
                    <span className="font-mono">{r.name}</span>
                    <span className="font-normal text-muted-foreground">in {r.factory}</span>
                  </>
                )}
              </ItemTitle>
              <ItemDescription>
                {r.lead}
                {r.picks.length > 0 && ` Suggests ${r.picks.length} skills.`}
              </ItemDescription>
            </ItemContent>
            <ItemActions>
              {on ? (
                <CheckIcon className="size-4 text-kin" />
              ) : (
                <CircleIcon className="size-4 text-muted-foreground/40" />
              )}
            </ItemActions>
          </Item>
        )
      })}
    </Rows>
  )
}

// ── Name and look ──────────────────────────────────────────────────────────────────────────────

const SHAPES: AvatarShape[] = ["circle", "squircle", "square"]
const COLORS: AvatarColor[] = ["blue", "violet", "green", "amber", "red", "gray"]

export function IdentityFields({
  draft,
  onChange,
  compact,
}: {
  draft: Draft
  onChange: (d: Draft) => void
  compact?: boolean
}) {
  const avatar = { shape: draft.shape, color: draft.color }
  const name = draft.name.trim() === "" ? "New" : draft.name
  return (
    <div className={cn("flex gap-4", compact ? "items-start" : "flex-col")}>
      <InstanceAvatar avatar={avatar} name={name} size="lg" />
      <FieldGroup className="min-w-0 flex-1 gap-4">
        <Field>
          <FieldLabel htmlFor="proto-name">Name</FieldLabel>
          <Input
            id="proto-name"
            value={draft.name}
            placeholder="Atlas"
            onChange={(e) => onChange({ ...draft, name: e.target.value })}
          />
        </Field>
        <div className="flex flex-wrap gap-4">
          <ToggleGroup
            aria-label="Shape"
            value={[draft.shape]}
            onValueChange={([s]) => s && onChange({ ...draft, shape: s as AvatarShape })}
          >
            {SHAPES.map((s) => (
              <ToggleGroupItem key={s} value={s} aria-label={s}>
                <InstanceAvatar avatar={{ ...avatar, shape: s }} name={name} size="sm" />
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <ToggleGroup
            aria-label="Color"
            className="flex-wrap"
            value={[draft.color]}
            onValueChange={([c]) => c && onChange({ ...draft, color: c as AvatarColor })}
          >
            {COLORS.map((c) => (
              <ToggleGroupItem key={c} value={c} aria-label={c}>
                <InstanceAvatar avatar={{ ...avatar, color: c }} name={name} size="sm" />
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      </FieldGroup>
    </div>
  )
}

// ── Skills ─────────────────────────────────────────────────────────────────────────────────────

export function SkillRows({ draft, onChange }: { draft: Draft; onChange: (d: Draft) => void }) {
  // Suggested picks first, then the rest of the library.
  const ordered = [
    ...OFFERED_SKILLS.filter((s) => draft.role.picks.includes(s.name)),
    ...OFFERED_SKILLS.filter((s) => !draft.role.picks.includes(s.name)),
  ]
  return (
    <Rows>
      {ordered.map((s) => {
        const on = draft.skills.includes(s.name)
        const id = `proto-skill-${s.name}`
        return (
          <Item key={s.name} size="sm" render={<label htmlFor={id} />} className="cursor-pointer">
            <Checkbox
              id={id}
              checked={on}
              onCheckedChange={(c) =>
                onChange({
                  ...draft,
                  skills: c ? [...draft.skills, s.name] : draft.skills.filter((n) => n !== s.name),
                })
              }
            />
            <ItemContent className="min-w-0">
              <ItemTitle>
                <span className="font-mono">{s.name}</span>
                {draft.role.picks.includes(s.name) && (
                  <Badge variant="secondary">Suggested by {draft.role.name}</Badge>
                )}
              </ItemTitle>
              <ItemDescription className="line-clamp-1">{s.description}</ItemDescription>
            </ItemContent>
          </Item>
        )
      })}
    </Rows>
  )
}

// ── Prompt ─────────────────────────────────────────────────────────────────────────────────────

/** The prompt folded to its first line, unfolding in place to edit. */
export function PromptRow({ draft, onChange }: { draft: Draft; onChange: (d: Draft) => void }) {
  const [open, setOpen] = useState(false)
  const edited = draft.prompt !== draft.role.prompt
  return (
    <Rows>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger
          render={
            <Item
              size="sm"
              className="w-full cursor-pointer text-left hover:bg-muted/50"
              render={<button type="button" />}
            />
          }
        >
          <ItemContent className="min-w-0">
            <ItemTitle>
              Behavior prompt
              {edited ? (
                <Badge variant="outline">Edited</Badge>
              ) : (
                <span className="font-normal text-muted-foreground">
                  {draft.role.factory === undefined ? "kinby's default" : `${draft.role.name}'s`}
                </span>
              )}
            </ItemTitle>
            <ItemDescription className="line-clamp-1">
              {draft.prompt.split("\n")[0]}
            </ItemDescription>
          </ItemContent>
          <ItemActions>
            <ChevronDownIcon
              className={cn(
                "size-4 text-muted-foreground transition-transform",
                open && "rotate-180",
              )}
            />
          </ItemActions>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex flex-col gap-2 border-t bg-muted/40 p-4">
            <Textarea
              aria-label="Behavior prompt"
              className="min-h-48 bg-background font-mono text-xs"
              value={draft.prompt}
              onChange={(e) => onChange({ ...draft, prompt: e.target.value })}
            />
            {edited && (
              <Button
                size="sm"
                variant="ghost"
                className="self-start"
                onClick={() => onChange({ ...draft, prompt: draft.role.prompt })}
              >
                Back to the default
              </Button>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </Rows>
  )
}

/** The prompt as a plain editor, for a step of its own. */
export function PromptEditor({ draft, onChange }: { draft: Draft; onChange: (d: Draft) => void }) {
  return (
    <Textarea
      aria-label="Behavior prompt"
      className="min-h-72 font-mono text-xs"
      value={draft.prompt}
      onChange={(e) => onChange({ ...draft, prompt: e.target.value })}
    />
  )
}

// ── Setup ──────────────────────────────────────────────────────────────────────────────────────

/** What the image and the role ask for, each row saying who asks. */
export function SetupRows({
  draft,
  fields,
  onChange,
}: {
  draft: Draft
  fields: MockField[]
  onChange: (d: Draft) => void
}) {
  return (
    <Rows>
      {fields.map((f) => (
        <Item key={f.name} size="sm" className="items-start">
          <ItemContent className="min-w-0 basis-56">
            <ItemTitle>
              {f.label}
              {!f.required && f.kind !== "login" && (
                <span className="font-normal text-muted-foreground">optional</span>
              )}
            </ItemTitle>
            <ItemDescription>
              {f.description}{" "}
              <span className="text-muted-foreground/70">
                · {f.askedBy === "kinby" ? "every instance" : `asked by ${f.askedBy}`}
              </span>
            </ItemDescription>
          </ItemContent>
          <div className="w-full sm:w-72">
            <SetupControl draft={draft} field={f} onChange={onChange} />
          </div>
        </Item>
      ))}
    </Rows>
  )
}

function SetupControl({
  draft,
  field: f,
  onChange,
}: {
  draft: Draft
  field: MockField
  onChange: (d: Draft) => void
}) {
  const value = draft.values[f.name] ?? ""
  const set = (v: string) => onChange({ ...draft, values: { ...draft.values, [f.name]: v } })
  if (f.kind === "login") {
    return (
      <Badge variant="outline" className="mt-1">
        Sign in after it's created
      </Badge>
    )
  }
  if (f.kind === "choice") {
    const items = (f.options ?? []).map((o) => ({ value: o, label: o }))
    return (
      <Select items={items} value={value} onValueChange={(v) => v && set(v)}>
        <SelectTrigger className="w-full font-mono text-xs" aria-label={f.label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {items.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    )
  }
  if (f.kind === "secret") {
    const linked = draft.linked[f.name]
    if (linked !== undefined) {
      return (
        <InputGroup>
          <InputGroupAddon>
            <KeyRoundIcon />
          </InputGroupAddon>
          <InputGroupInput
            readOnly
            aria-label={f.label}
            value={linked}
            className="font-mono text-xs"
          />
          <InputGroupAddon align="inline-end">
            <InputGroupButton
              size="xs"
              onClick={() => {
                const { [f.name]: _, ...rest } = draft.linked
                onChange({ ...draft, linked: rest })
              }}
            >
              Type one
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
      )
    }
    return (
      <InputGroup>
        <InputGroupInput
          type="password"
          aria-label={f.label}
          placeholder="Paste the secret"
          value={value}
          onChange={(e) => set(e.target.value)}
        />
        {f.hubSecret !== undefined && (
          <InputGroupAddon align="inline-end">
            <DropdownMenu>
              <DropdownMenuTrigger render={<InputGroupButton size="xs" />}>
                Hub secret
                <ChevronDownIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Use a hub secret</DropdownMenuLabel>
                  <DropdownMenuItem
                    onClick={() =>
                      onChange({
                        ...draft,
                        linked: { ...draft.linked, [f.name]: f.hubSecret ?? "" },
                      })
                    }
                  >
                    <KeyRoundIcon />
                    <span className="font-mono text-xs">{f.hubSecret}</span>
                  </DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </InputGroupAddon>
        )}
      </InputGroup>
    )
  }
  return (
    <Input
      aria-label={f.label}
      type={f.kind === "url" ? "url" : "text"}
      placeholder={f.kind === "url" ? "https://github.com/you/repo" : ""}
      value={value}
      onChange={(e) => set(e.target.value)}
    />
  )
}

// ── Creating ───────────────────────────────────────────────────────────────────────────────────

/** The creation's steps, then the sign-ins it declares, then Start. */
export function Creating({
  draft,
  onStarted,
  onBack,
}: {
  draft: Draft
  onStarted: () => void
  onBack: () => void
}) {
  const [done, setDone] = useState(0)
  const [signedIn, setSignedIn] = useState(false)
  const [starting, setStarting] = useState(false)
  useEffect(() => {
    if (done >= CREATE_STEPS.length) return
    const t = window.setTimeout(() => setDone((n) => n + 1), 600)
    return () => window.clearTimeout(t)
  }, [done])
  const created = done >= CREATE_STEPS.length
  const login = draft.role.factory !== undefined
  const name = draft.name.trim()

  return (
    <div className="flex flex-col gap-8">
      <Section title={created ? `${name} is created` : `Creating ${name}`}>
        <Rows>
          {CREATE_STEPS.map((s, n) => (
            <Item key={s} size="xs">
              {n < done ? (
                <CheckIcon className="size-4 text-kin" />
              ) : n === done ? (
                <Spinner className="size-4" />
              ) : (
                <CircleIcon className="size-4 text-muted-foreground/40" />
              )}
              <ItemContent>
                <ItemTitle className={cn(n > done && "text-muted-foreground")}>{s}</ItemTitle>
              </ItemContent>
            </Item>
          ))}
        </Rows>
        {!created && (
          <Button variant="ghost" size="sm" className="self-start" onClick={onBack}>
            Back to the form
          </Button>
        )}
      </Section>
      {created && login && (
        <Section
          title="Sign in"
          lead="Open the link and enter the code there. Starting doesn't wait for it."
        >
          <Rows>
            <Item size="sm">
              <ItemContent>
                <ItemTitle>
                  Claude Code
                  {signedIn ? (
                    <Badge variant="secondary">Signed in</Badge>
                  ) : (
                    <span className="font-normal text-muted-foreground">waiting</span>
                  )}
                </ItemTitle>
                {!signedIn && (
                  <ItemDescription>
                    Code <span className="font-mono text-foreground">{LOGIN.code}</span>
                  </ItemDescription>
                )}
              </ItemContent>
              {!signedIn && (
                <ItemActions>
                  <Button size="sm" variant="ghost" aria-label="Copy the code">
                    <CopyIcon />
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setSignedIn(true)}>
                    Open the link
                    <ExternalLinkIcon data-icon="inline-end" />
                  </Button>
                </ItemActions>
              )}
            </Item>
          </Rows>
        </Section>
      )}
      {created && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            disabled={starting}
            onClick={() => {
              setStarting(true)
              window.setTimeout(onStarted, 900)
            }}
          >
            {starting ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <PlayIcon data-icon="inline-start" />
            )}
            Start and chat
          </Button>
          <Button variant="ghost" disabled={starting} onClick={onStarted}>
            Leave it stopped
          </Button>
        </div>
      )}
    </div>
  )
}
