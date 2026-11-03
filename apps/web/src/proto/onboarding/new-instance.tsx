// PROTOTYPE · New instance, two variants. A "One page": every choice on one scroll in the Rows
// grammar, the header holding the one action (Create). B "Steps": one choice at a time, a rail of
// steps on the left and a summary of the instance so far under it.
import { CheckIcon } from "lucide-react"
import { useState } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { cn } from "cn"

import {
  Creating,
  IdentityFields,
  ImageStatus,
  PromptEditor,
  PromptRow,
  RoleRows,
  Section,
  SetupRows,
  SkillRows,
  useDraft,
  useImage,
} from "./shared"

// ── A · One page ───────────────────────────────────────────────────────────────────────────────

export function NewInstanceA({ onDone }: { onDone: () => void }) {
  const { draft, setDraft, pickRole, fields, missing } = useDraft()
  const image = useImage()
  const [creating, setCreating] = useState(false)
  const named = draft.name.trim() !== ""
  const blocked = !image.ready
    ? "The image is still preparing"
    : !named
      ? "Name it"
      : missing.length > 0
        ? `${missing.length} setup ${missing.length === 1 ? "field" : "fields"} left`
        : undefined

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 bg-background">
        <div className="mx-auto flex w-full max-w-3xl items-center gap-3 px-4 py-5 md:px-8">
          <InstanceAvatar
            avatar={{ shape: draft.shape, color: draft.color }}
            name={named ? draft.name : "New"}
          />
          <div className="flex min-w-0 flex-1 flex-col">
            <h1 className="truncate font-heading text-xl font-semibold">
              {named ? draft.name : "New instance"}
            </h1>
            <ImageStatus image={image} />
          </div>
          {!creating && (
            <div className="flex items-center gap-3">
              {blocked && (
                <span className="hidden text-xs text-muted-foreground sm:inline">{blocked}</span>
              )}
              <Button disabled={blocked !== undefined} onClick={() => setCreating(true)}>
                Create
              </Button>
            </div>
          )}
        </div>
        <Separator />
      </div>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-10 px-4 py-8 md:px-8">
        {creating ? (
          <Creating draft={draft} onStarted={onDone} onBack={() => setCreating(false)} />
        ) : (
          <>
            <Section
              title="Role"
              lead="A factory role brings its own prompt, skills and setup. It's kept for life."
            >
              <RoleRows draft={draft} onPick={pickRole} />
            </Section>
            <Section title="Name and look">
              <IdentityFields draft={draft} onChange={setDraft} compact />
            </Section>
            <Section
              title="Skills"
              lead="Each one ticked is copied in. You can add or drop skills later."
              aside={
                <span className="text-xs text-muted-foreground">{draft.skills.length} picked</span>
              }
            >
              <SkillRows draft={draft} onChange={setDraft} />
            </Section>
            <Section title="Prompt" lead="The instructions it follows on every turn.">
              <PromptRow draft={draft} onChange={setDraft} />
            </Section>
            <Section
              title="Setup"
              lead="What the image and the role ask for. Secrets are write-only."
              aside={
                missing.length > 0 && (
                  <span className="text-xs text-muted-foreground">{missing.length} left</span>
                )
              }
            >
              <SetupRows draft={draft} fields={fields} onChange={setDraft} />
            </Section>
          </>
        )}
      </div>
    </div>
  )
}

// ── B · Steps ──────────────────────────────────────────────────────────────────────────────────

const STEPS = ["Role", "Name and look", "Skills", "Prompt", "Setup", "Review"] as const

export function NewInstanceB({ onDone }: { onDone: () => void }) {
  const { draft, setDraft, pickRole, fields, missing } = useDraft()
  const image = useImage()
  const [step, setStep] = useState(0)
  const [reached, setReached] = useState(0)
  const [creating, setCreating] = useState(false)
  const named = draft.name.trim() !== ""
  const go = (n: number) => {
    setStep(n)
    setReached((r) => Math.max(r, n))
  }
  const canContinue = step === 1 ? named : step === 4 ? missing.length === 0 : true

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-4 py-8 md:flex-row md:px-8">
        <aside className="flex shrink-0 flex-col gap-6 md:sticky md:top-8 md:w-60 md:self-start">
          <div className="flex flex-col gap-1">
            <h1 className="font-heading text-xl font-semibold">New instance</h1>
            <ImageStatus image={image} />
          </div>
          {/* A phone keeps only the progress line. */}
          <div className="flex flex-col gap-2 md:hidden">
            <span className="text-xs text-muted-foreground">
              Step {step + 1} of {STEPS.length} · {STEPS[step]}
            </span>
            <Progress value={((step + 1) / STEPS.length) * 100} />
          </div>
          <ol className="hidden flex-col gap-0.5 md:flex">
            {STEPS.map((s, n) => (
              <li key={s}>
                <button
                  type="button"
                  disabled={n > reached || creating}
                  onClick={() => go(n)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-sm",
                    n === step ? "bg-muted font-medium" : "hover:bg-muted/50",
                    n > reached && "text-muted-foreground hover:bg-transparent",
                  )}
                >
                  <span
                    className={cn(
                      "grid size-5 shrink-0 place-items-center rounded-full border text-[10px] font-medium",
                      n < reached && n !== step && "border-transparent bg-kin text-white",
                      n === step && "border-foreground",
                    )}
                  >
                    {n < reached && n !== step ? <CheckIcon className="size-3" /> : n + 1}
                  </span>
                  {s}
                </button>
              </li>
            ))}
          </ol>
          <Summary draft={draft} missing={missing.length} />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col gap-6">
          {creating ? (
            <Creating draft={draft} onStarted={onDone} onBack={() => setCreating(false)} />
          ) : (
            <>
              <StepBody
                step={step}
                draft={draft}
                setDraft={setDraft}
                pickRole={pickRole}
                fields={fields}
                go={go}
              />
              <div className="flex items-center gap-2 border-t pt-4">
                {step > 0 && (
                  <Button variant="ghost" onClick={() => go(step - 1)}>
                    Back
                  </Button>
                )}
                <div className="ml-auto" />
                {step < STEPS.length - 1 ? (
                  <Button disabled={!canContinue} onClick={() => go(step + 1)}>
                    Continue
                  </Button>
                ) : (
                  <Button
                    disabled={!image.ready || missing.length > 0 || !named}
                    onClick={() => setCreating(true)}
                  >
                    {image.ready ? "Create" : "Waiting for the image…"}
                  </Button>
                )}
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  )
}

function StepBody({
  step,
  draft,
  setDraft,
  pickRole,
  fields,
  go,
}: {
  step: number
  go: (n: number) => void
} & Pick<ReturnType<typeof useDraft>, "draft" | "setDraft" | "pickRole" | "fields">) {
  switch (step) {
    case 0:
      return (
        <Section
          title="Pick a role"
          lead="A factory role brings its own prompt, skills and setup, and it's kept for life. Pick None for a teammate of your own."
        >
          <RoleRows draft={draft} onPick={pickRole} />
        </Section>
      )
    case 1:
      return (
        <Section
          title="Name it and pick its look"
          lead="It shows by this name and avatar everywhere."
        >
          <IdentityFields draft={draft} onChange={setDraft} />
        </Section>
      )
    case 2:
      return (
        <Section
          title="Pick its skills"
          lead={
            draft.role.picks.length > 0
              ? `${draft.role.name} suggests ${draft.role.picks.length}; they start ticked. You can add or drop skills later.`
              : "Each one ticked is copied in. You can add or drop skills later."
          }
        >
          <SkillRows draft={draft} onChange={setDraft} />
        </Section>
      )
    case 3:
      return (
        <Section
          title="Write its prompt"
          lead="The instructions it follows on every turn. It starts from the default."
        >
          <PromptEditor draft={draft} onChange={setDraft} />
        </Section>
      )
    case 4:
      return (
        <Section
          title="Fill in its setup"
          lead="What the image and the role ask for. Secrets are write-only."
        >
          <SetupRows draft={draft} fields={fields} onChange={setDraft} />
        </Section>
      )
    default:
      return (
        <Section title="Review" lead="Create it, then start it or leave it stopped.">
          <dl className="grid grid-cols-[8rem_1fr_auto] items-baseline gap-x-4 gap-y-3 rounded-xl border p-4 text-sm">
            <Line
              label="Role"
              value={draft.role.factory ? `${draft.role.name} in ${draft.role.factory}` : "None"}
              edit={() => go(0)}
            />
            <Line label="Name" value={draft.name} edit={() => go(1)} />
            <Line label="Skills" value={draft.skills.join(", ") || "None"} edit={() => go(2)} />
            <Line
              label="Prompt"
              value={draft.prompt === draft.role.prompt ? "The default" : "Edited"}
              edit={() => go(3)}
            />
            <Line
              label="Setup"
              value={`${fields.filter((f) => f.kind !== "login").length} fields${fields.some((f) => f.kind === "login") ? " · one sign-in after creation" : ""}`}
              edit={() => go(4)}
            />
          </dl>
        </Section>
      )
  }
}

function Line({ label, value, edit }: { label: string; value: string; edit: () => void }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate">{value}</dd>
      <Button size="xs" variant="ghost" onClick={edit}>
        Edit
      </Button>
    </>
  )
}

function Summary({
  draft,
  missing,
}: {
  draft: ReturnType<typeof useDraft>["draft"]
  missing: number
}) {
  const named = draft.name.trim() !== ""
  return (
    <div className="hidden flex-col gap-3 rounded-xl border p-4 md:flex">
      <div className="flex items-center gap-3">
        <InstanceAvatar
          avatar={{ shape: draft.shape, color: draft.color }}
          name={named ? draft.name : "New"}
        />
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium">{named ? draft.name : "Unnamed"}</span>
          <span className="truncate text-xs text-muted-foreground">
            {draft.role.factory ? `${draft.role.name} in ${draft.role.factory}` : "No role"}
          </span>
        </div>
      </div>
      <Separator />
      <ul className="flex flex-col gap-1 text-xs text-muted-foreground">
        <li>{draft.skills.length} skills</li>
        <li>{draft.prompt === draft.role.prompt ? "Default prompt" : "Edited prompt"}</li>
        <li>{missing === 0 ? "Setup complete" : `${missing} setup fields left`}</li>
      </ul>
    </div>
  )
}
