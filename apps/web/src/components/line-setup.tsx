import { CallError } from "@kinby/contract"
import type {
  Client,
  Clock,
  LineSetupCommand,
  LineSetupDescription,
  SetupField,
  SetupValue,
  Workbench,
} from "@kinby/contract"
import { ChevronRightIcon, CircleXIcon, PlusIcon } from "lucide-react"
import { type FormEvent, type ReactNode, useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { SetupInput } from "@/components/onboarding"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"
import { type FieldInput, initialInput, setupValue } from "@/lib/creation"
import { readSetup } from "@/lib/lines"
import { reason } from "@/lib/operation"
import { linePath, navigate } from "@/lib/selection"

type Caller = Pick<Client, "call">

/** Where the setup stands: picking the factory, then the workbench, then answering what is missing. */
type Stage =
  | { at: "factory" }
  | { at: "workbench"; factory: string }
  /** A new workbench is no `workbench`. */
  | { at: "answers"; factory: string; workbench?: Workbench; asked: LineSetupDescription }

/**
 * Set up a line, in a dialog: pick one of `factories`, then one of `workbenches` or a new one, then
 * answer only what the factory requires and the workbench lacks. A set-up line opens its page.
 */
export function SetUpLine({
  caller,
  clock,
  factories,
  workbenches,
}: {
  caller: Caller
  clock: Clock
  factories: string[]
  workbenches: Workbench[]
}) {
  return (
    <Dialog>
      <DialogTrigger render={<Button />}>
        <PlusIcon data-icon="inline-start" />
        Set up a line
      </DialogTrigger>
      <DialogContent className="max-h-9/10 overflow-y-auto">
        <SetupFlow caller={caller} clock={clock} factories={factories} workbenches={workbenches} />
      </DialogContent>
    </Dialog>
  )
}

function SetupFlow({
  caller,
  clock,
  factories,
  workbenches,
}: {
  caller: Caller
  clock: Clock
  factories: string[]
  workbenches: Workbench[]
}) {
  const [stage, setStage] = useState<Stage>({ at: "factory" })
  const lead =
    stage.at === "factory"
      ? "Which factory?"
      : stage.at === "workbench"
        ? `Where does ${stage.factory} run?`
        : `What ${stage.factory} still needs${stage.workbench ? ` on ${stage.workbench.name}` : ""}`
  return (
    <>
      <DialogHeader>
        <DialogTitle>Set up a line</DialogTitle>
        <DialogDescription>{lead}</DialogDescription>
      </DialogHeader>
      {stage.at === "factory" && (
        <Picks label="Factories">
          {factories.map((name) => (
            <Pick
              key={name}
              label={name}
              title={<span className="font-mono">{name}</span>}
              onPick={() => setStage({ at: "workbench", factory: name })}
            />
          ))}
        </Picks>
      )}
      {stage.at === "workbench" && (
        <WorkbenchPicks
          caller={caller}
          clock={clock}
          factory={stage.factory}
          workbenches={workbenches}
          onBack={() => setStage({ at: "factory" })}
          onPick={(workbench, asked) =>
            setStage({ at: "answers", factory: stage.factory, workbench, asked })
          }
        />
      )}
      {stage.at === "answers" && (
        <Answers
          caller={caller}
          factory={stage.factory}
          workbench={stage.workbench}
          asked={stage.asked}
          onBack={() => setStage({ at: "workbench", factory: stage.factory })}
        />
      )}
    </>
  )
}

/** Each workbench and New workbench, one that cannot take the line disabled with why. */
function WorkbenchPicks({
  caller,
  clock,
  factory,
  workbenches,
  onBack,
  onPick,
}: {
  caller: Caller
  clock: Clock
  factory: string
  workbenches: Workbench[]
  onBack: () => void
  onPick: (workbench: Workbench | undefined, asked: LineSetupDescription) => void
}) {
  const read = useCallback(
    () => readSetup(caller, factory, workbenches),
    [caller, factory, workbenches],
  )
  const { value: asks, failure } = useRead(read, clock)
  return (
    <>
      {failure !== undefined && <Failure error={failure} />}
      {asks === undefined ? (
        failure === undefined && <Skeleton className="h-32 w-full" />
      ) : (
        <div className="flex flex-col gap-3">
          {asks.fits.length > 0 && (
            <Picks label="Workbenches">
              {asks.fits.map((fit) => {
                const { workbench } = fit
                return (
                  <Pick
                    key={workbench.workbench_id}
                    label={workbench.name}
                    title={workbench.name}
                    description={
                      "refused" in fit
                        ? fit.refused
                        : `${workbench.recipe ?? "default"} · ${workbench.workspace_source ?? "A plain folder"}`
                    }
                    disabled={"refused" in fit}
                    onPick={() => "asked" in fit && onPick(workbench, fit.asked)}
                  />
                )
              })}
            </Picks>
          )}
          <Button
            variant="secondary"
            disabled={"refused" in asks.fresh}
            onClick={() => "asked" in asks.fresh && onPick(undefined, asks.fresh.asked)}
          >
            <PlusIcon data-icon="inline-start" />
            New workbench
          </Button>
          {"refused" in asks.fresh && <p className="text-muted-foreground">{asks.fresh.refused}</p>}
        </div>
      )}
      <DialogFooter>
        <Button variant="ghost" onClick={onBack}>
          Back
        </Button>
      </DialogFooter>
    </>
  )
}

function Picks({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ItemGroup variant="outline" aria-label={label}>
      {children}
    </ItemGroup>
  )
}

function Pick({
  label,
  title,
  description,
  disabled = false,
  onPick,
}: {
  label: string
  title: ReactNode
  description?: string
  disabled?: boolean
  onPick: () => void
}) {
  return (
    <li>
      <Item
        size="sm"
        render={<button type="button" aria-label={label} disabled={disabled} />}
        onClick={onPick}
      >
        <ItemContent className="min-w-0">
          <ItemTitle>{title}</ItemTitle>
          {description !== undefined && (
            <ItemDescription lines="all">{description}</ItemDescription>
          )}
        </ItemContent>
        {!disabled && (
          <ItemActions>
            <ChevronRightIcon className="text-muted-foreground" />
          </ItemActions>
        )}
      </Item>
    </li>
  )
}

/** The key a new workbench's own field is answered and refused under. */
const workbenchKey = (field: SetupField) => `workbench.${field.name}`

/**
 * What the factory still needs: a new workbench's own fields, then each secret, named value and
 * list of repository commands asked. A refusal shows under the field it names, and the rest of it,
 * such as what the workbench still lacks, above the buttons.
 */
function Answers({
  caller,
  factory,
  workbench,
  asked,
  onBack,
}: {
  caller: Caller
  factory: string
  workbench: Workbench | undefined
  asked: LineSetupDescription
  onBack: () => void
}) {
  const id = useId()
  const [inputs, setInputs] = useState<Record<string, FieldInput>>({})
  const [commands, setCommands] = useState<Record<string, string>>({})
  const [refusal, setRefusal] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const answer = (key: string, input: FieldInput) =>
    setInputs((held) => ({ ...held, [key]: input }))
  const fields = [
    ...asked.workbench.map((field) => ({ key: workbenchKey(field), field })),
    ...[...asked.secrets, ...asked.values].map((field) => ({ key: field.name, field })),
  ]
  const shown = new Set(fields.map(({ key }) => key))
  const unshown = Object.entries(refusal).filter(([key]) => !shown.has(key))

  const setUp = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setRefusal({})
    try {
      const { line } = await caller.call(
        "line.setup",
        setupCommand(factory, workbench, asked, inputs, commands),
      )
      navigate(linePath(line.line_id))
    } catch (error) {
      setRefusal(
        error instanceof CallError && error.code === "INVALID_SETUP"
          ? error.fields
          : { setup: reason(error) },
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <form aria-label="What is missing" className="flex flex-col gap-6" onSubmit={setUp}>
      <FieldGroup>
        {fields.map(({ key, field }) => (
          <SetupInput
            key={key}
            field={field}
            value={inputs[key] ?? initialInput(field)}
            error={refusal[key]}
            onChange={(input) => answer(key, input)}
          />
        ))}
        {asked.commands.map((name) => (
          <Field key={name}>
            <FieldLabel htmlFor={`${id}-${name}`}>{name} commands</FieldLabel>
            <Textarea
              id={`${id}-${name}`}
              value={commands[name] ?? ""}
              onChange={(event) => setCommands((held) => ({ ...held, [name]: event.target.value }))}
            />
            <FieldDescription>One command per line. None is a valid list.</FieldDescription>
          </Field>
        ))}
        {fields.length === 0 && asked.commands.length === 0 && workbench !== undefined && (
          <p className="text-muted-foreground">
            {workbench.name} holds everything {factory} requires.
          </p>
        )}
      </FieldGroup>
      {unshown.length > 0 && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The hub refused the setup</AlertTitle>
          <AlertDescription>
            <ul>
              {unshown.map(([key, message]) => (
                <li key={key}>{message}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      <DialogFooter>
        <Button type="button" variant="ghost" disabled={saving} onClick={onBack}>
          Back
        </Button>
        <Button type="submit" disabled={saving}>
          {saving && <Spinner data-icon="inline-start" />}
          Set up
        </Button>
      </DialogFooter>
    </form>
  )
}

/**
 * The setup command: on the chosen workbench, or on a new one named by its own fields. Blank text
 * answers nothing, so the hub names what is still missing, and each list of commands goes one per
 * line.
 */
function setupCommand(
  factory: string,
  workbench: Workbench | undefined,
  asked: LineSetupDescription,
  inputs: Record<string, FieldInput>,
  commands: Record<string, string>,
): LineSetupCommand {
  const answered = (key: string, field: SetupField): SetupValue | undefined => {
    const input = inputs[key] ?? initialInput(field)
    if (input === undefined || (typeof input === "string" && input.trim() === "")) return undefined
    return setupValue(field, input)
  }
  const answers = (fields: SetupField[]) =>
    Object.fromEntries(
      fields.flatMap((field) => {
        const value = answered(field.name, field)
        return value === undefined ? [] : [[field.name, value]]
      }),
    )
  const own = Object.fromEntries(
    asked.workbench.map((field) => [field.name, answered(workbenchKey(field), field)]),
  )
  return {
    factory,
    ...(workbench === undefined
      ? {
          workbench: {
            name: String(own.name ?? ""),
            workspace_source:
              own.workspace_source === undefined ? null : String(own.workspace_source),
          },
        }
      : { workbench_id: workbench.workbench_id }),
    secrets: answers(asked.secrets) as Record<string, string>,
    values: answers(asked.values),
    commands: Object.fromEntries(
      asked.commands.map((name) => [
        name,
        (commands[name] ?? "")
          .split("\n")
          .map((command) => command.trim())
          .filter((command) => command !== ""),
      ]),
    ),
  }
}
