import type { InstanceClient, PromptName } from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { reason } from "@/lib/operation"
import { lastChanged, type OpenedPrompt, openPrompt, PROMPT_FILES, savePrompt } from "@/lib/prompts"
import {
  BoxIcon,
  CircleXIcon,
  CpuIcon,
  FileTextIcon,
  KeyRoundIcon,
  type LucideIcon,
  NotebookPenIcon,
  PackageIcon,
  RepeatIcon,
  ShieldIcon,
  SparklesIcon,
  WrenchIcon,
} from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/** One section of the panel. Only a section with a `prompt` is built yet; the rest are unavailable. */
interface Section {
  label: string
  hint: string
  icon: LucideIcon
  prompt?: PromptName
}

const GROUPS: { label: string; sections: Section[] }[] = [
  {
    label: "Behavior",
    sections: [
      {
        label: "Behavior prompt",
        hint: "SYSTEM.md, how the agent acts",
        icon: FileTextIcon,
        prompt: "behavior",
      },
      {
        label: "Recap prompt",
        hint: "RECAP.md, what a recap looks at",
        icon: NotebookPenIcon,
        prompt: "recap",
      },
      { label: "Permissions", hint: "ceiling, mode, tool rules, shell patterns", icon: ShieldIcon },
      { label: "Manifest", hint: "models, budgets, timezone", icon: CpuIcon },
    ],
  },
  {
    label: "Capabilities",
    sections: [
      { label: "Routines", hint: "schedules, signals, next firing", icon: RepeatIcon },
      { label: "Skills", hint: "instance, package, workspace", icon: SparklesIcon },
      { label: "Tools", hint: "what the instance can do", icon: WrenchIcon },
    ],
  },
  {
    label: "Instance",
    sections: [
      { label: "Secrets and login", hint: "write-only values, sign-in", icon: KeyRoundIcon },
      { label: "Package config", hint: "the package's own settings", icon: BoxIcon },
      { label: "Package and version", hint: "template, installed, update", icon: PackageIcon },
    ],
  },
]

/** When a saved prompt takes effect. */
const APPLIES: Record<PromptName, string> = {
  behavior: "Saved. It applies at the next turn.",
  recap: "Saved. It applies at the next recap.",
}

/** The instance's config: its sections grouped in a left column, the selected one on the right. */
export function ConfigPanel({ client }: { client: Caller }) {
  const [selected, setSelected] = useState<PromptName>("behavior")
  const section = GROUPS.flatMap((group) => group.sections).find(
    (candidate) => candidate.prompt === selected,
  )
  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <nav
        aria-label="Config sections"
        className="flex shrink-0 flex-col gap-4 overflow-y-auto border-b p-3 md:w-72 md:border-r md:border-b-0"
      >
        {GROUPS.map((group) => (
          <SectionGroup
            key={group.label}
            label={group.label}
            sections={group.sections}
            selected={selected}
            onSelect={setSelected}
          />
        ))}
      </nav>
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-6">
        <div>
          <h1 className="text-xl font-semibold">{section?.label}</h1>
          <p className="text-sm text-muted-foreground">{section?.hint}</p>
        </div>
        <Separator />
        <PromptSection key={selected} client={client} name={selected} />
      </main>
    </div>
  )
}

/** One group of the left column, as a list named for the group. */
function SectionGroup({
  label,
  sections,
  selected,
  onSelect,
}: {
  label: string
  sections: Section[]
  selected: PromptName
  onSelect: (prompt: PromptName) => void
}) {
  const labelId = useId()
  return (
    <div className="flex flex-col gap-1">
      <span id={labelId} className="px-3 text-xs font-medium text-muted-foreground">
        {label}
      </span>
      <ItemGroup aria-labelledby={labelId}>
        {sections.map(({ label, hint, icon: Icon, prompt }) => (
          <li key={label}>
            <Item
              size="xs"
              variant={prompt !== undefined && prompt === selected ? "muted" : "default"}
              render={
                <button
                  type="button"
                  aria-label={label}
                  aria-current={prompt === selected || undefined}
                  disabled={prompt === undefined}
                  onClick={() => prompt !== undefined && onSelect(prompt)}
                />
              }
            >
              <ItemMedia variant="icon">
                <Icon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{label}</ItemTitle>
                <ItemDescription>
                  {prompt === undefined ? "Not available yet" : hint}
                </ItemDescription>
              </ItemContent>
            </Item>
          </li>
        ))}
      </ItemGroup>
    </div>
  )
}

/**
 * One prompt as text. A save carries the hash of what was read, so a save over a change made
 * since is refused, and "Load theirs" reads the prompt again.
 */
function PromptSection({ client, name }: { client: Caller; name: PromptName }) {
  const id = useId()
  const file = PROMPT_FILES[name]
  const [opened, setOpened] = useState<OpenedPrompt>()
  const [draft, setDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [failure, setFailure] = useState<string>()

  const show = useCallback((read: OpenedPrompt) => {
    setOpened(read)
    setDraft(read.prompt.content)
  }, [])
  const load = useCallback(
    () =>
      openPrompt(client, name).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(reason(error)),
      ),
    [client, name, show],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure>{failure}</Failure>
    )
  }
  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      const saved = await savePrompt(client, name, draft, opened.prompt.hash)
      if (saved.state === "saved") show(saved.opened)
      setNotice(saved.state)
    } catch (error) {
      setFailure(reason(error))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {notice === "stale" && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>Changed since you opened it</AlertTitle>
          <AlertDescription>
            {file} was saved by someone else, most likely the agent. Load theirs to see it. Your
            edits here are dropped.
          </AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={() => void load()}>
              Load theirs
            </Button>
          </AlertAction>
        </Alert>
      )}
      {failure !== undefined && <Failure>{failure}</Failure>}
      <Field>
        <div className="flex items-center gap-2">
          <FieldLabel htmlFor={id}>{file}</FieldLabel>
          {opened.prompt.default && <Badge variant="secondary">Shipped default</Badge>}
        </div>
        {opened.prompt.default && (
          <FieldDescription>
            The instance has no {file}, so this is the text kinby ships. Saving writes the file.
          </FieldDescription>
        )}
        <Textarea
          id={id}
          className="min-h-72"
          value={draft}
          readOnly={saving}
          onChange={(event) => {
            setDraft(event.target.value)
            if (notice === "saved") setNotice(undefined)
          }}
        />
        <FieldDescription>{lastChanged(opened.lastChange)}</FieldDescription>
      </Field>
      <div className="flex items-center gap-3">
        <Button disabled={saving || draft === opened.prompt.content} onClick={() => void save()}>
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">{APPLIES[name]}</span>
        )}
      </div>
    </div>
  )
}

function Failure({ children }: { children: string }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>The instance did not answer</AlertTitle>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}
