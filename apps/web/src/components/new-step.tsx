import { useId, useState } from "react"
import { parse } from "yaml"

import { Refusal } from "@/components/config-alerts"
import { Choice, Code, type StepSaving, Text, Unsaved } from "@/components/step-editor"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useHubCall } from "@/hooks/use-hub-call"
import { ENGINES, type FactoryStep } from "@/lib/factory-steps"

const KINDS = ["agent", "command", "wait"]
const NO_CHECK = "none"

/** A dashed card in place, after the step `after`, for a new agent, command or wait step there.
 * A new agent step gets a new prompt file, and no check or one of the factory's checks. */
export function NewStep({
  after,
  steps,
  commands,
  saving,
  onAdded,
  onCancel,
}: {
  after: string
  steps: FactoryStep[]
  /** The names of the workbench's command lists the factory requires. */
  commands: string[]
  saving: StepSaving
  /** Runs once the add made a new version, with the new step's id. */
  onAdded: (id: string) => void
  onCancel: () => void
}) {
  const [kind, setKind] = useState("agent")
  const [id, setId] = useState("")
  const [description, setDescription] = useState("")
  const [engine, setEngine] = useState("claude")
  const [check, setCheck] = useState(NO_CHECK)
  const [prompt, setPrompt] = useState("")
  const [lists, setLists] = useState<string[]>([])
  const [signal, setSignal] = useState("")
  const [quiet, setQuiet] = useState("")
  const [notYaml, setNotYaml] = useState<string>()
  const { busy, refusal, failure, ask } = useHubCall()
  const listId = useId()
  const checks = [...new Set(steps.flatMap((step) => (step.check == null ? [] : [step.check])))]
  const promptPath = `prompts/${id}.md`
  const ready =
    id !== "" &&
    (kind !== "command" || lists.length > 0) &&
    (kind !== "wait" || signal.trim() !== "")

  const add = () => {
    const step: FactoryStep = { id, kind, ...(description !== "" && { description }) }
    if (kind === "agent") {
      Object.assign(step, { engine, prompt: promptPath }, checked(steps, check))
    } else if (kind === "command") {
      step.run = commands.filter((list) => lists.includes(list)).map((list) => ({ commands: list }))
    } else {
      try {
        step.signal = parse(signal)
      } catch (error) {
        setNotYaml(`The signal is not YAML: ${(error as Error).message}`)
        return
      }
      if (quiet !== "") step.quiet = quiet
    }
    setNotYaml(undefined)
    void ask(async () => {
      await saving.client.call("factory.step.add", {
        name: saving.factory.name,
        hash: saving.factory.hash,
        after,
        step,
        prompt: kind === "agent" ? prompt : null,
      })
      onAdded(id)
    })
  }

  return (
    <Card size="sm" variant="new">
      <CardHeader>
        <CardTitle>
          New step after <span className="font-mono">{after}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form
          aria-label={`New step after ${after}`}
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            add()
          }}
        >
          <FieldGroup className="grid md:grid-cols-2">
            <FieldSet className="md:col-span-2">
              <FieldLegend variant="label">Kind</FieldLegend>
              <ToggleGroup
                variant="outline"
                value={[kind]}
                onValueChange={([picked]) => {
                  if (typeof picked === "string") setKind(picked)
                }}
              >
                {KINDS.map((each) => (
                  <ToggleGroupItem key={each} value={each}>
                    {each}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
              <FieldDescription>
                Code steps come from the factory&apos;s files or from chat.
              </FieldDescription>
            </FieldSet>
            <Text label="Id" value={id} onChange={setId} />
            <Text label="Description" value={description} onChange={setDescription} />
            {kind === "agent" && (
              <>
                <Choice
                  label="Engine"
                  value={engine}
                  choices={ENGINES.map((each) => ({ value: each, label: each }))}
                  onChange={setEngine}
                />
                <Choice
                  label="Check"
                  value={check}
                  choices={[
                    { value: NO_CHECK, label: "No check" },
                    ...checks.map((each) => ({ value: each, label: each })),
                  ]}
                  onChange={setCheck}
                />
              </>
            )}
            {kind === "command" && (
              <FieldSet>
                <FieldLegend variant="label">Runs the workbench&apos;s lists</FieldLegend>
                {commands.length === 0 && (
                  <FieldDescription>
                    The factory requires no command list of its workbench.
                  </FieldDescription>
                )}
                {commands.map((list) => (
                  <Field key={list} orientation="horizontal">
                    <Checkbox
                      id={`${listId}${list}`}
                      checked={lists.includes(list)}
                      onCheckedChange={(on) =>
                        setLists(on ? [...lists, list] : lists.filter((each) => each !== list))
                      }
                    />
                    <FieldLabel htmlFor={`${listId}${list}`}>
                      <span className="font-mono">{list}</span>
                    </FieldLabel>
                  </Field>
                ))}
              </FieldSet>
            )}
            {kind === "wait" && <Text label="Quiet" value={quiet} onChange={setQuiet} />}
          </FieldGroup>
          {kind === "agent" && (
            <Code label={`Prompt · ${promptPath}`} value={prompt} onChange={setPrompt} />
          )}
          {kind === "wait" && <Code label="Signal" value={signal} onChange={setSignal} />}
          {notYaml !== undefined && <Refusal reason={notYaml} />}
          {refusal !== undefined && <Refusal reason={refusal} />}
          {failure !== undefined && <Unsaved error={failure} />}
          <div className="flex justify-end gap-2 border-t pt-3">
            <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ready || busy}>
              Add
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

/** What a new agent step with `check` takes over from the first step with that check: the values
 * the check reads and records, and the outcomes it names. */
function checked(steps: FactoryStep[], check: string): Partial<FactoryStep> {
  if (check === NO_CHECK) return {}
  const { requires, results, outcomes } = steps.find((step) => step.check === check) ?? {}
  return {
    check,
    ...(requires !== undefined && { requires }),
    ...(results !== undefined && { results }),
    ...(outcomes !== undefined && { outcomes }),
  }
}
