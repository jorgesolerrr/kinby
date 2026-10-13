import { useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"

/** Write a new skill's SKILL.md. Nothing once it is written, or the refusal by field. */
export type WriteNew = (
  name: string,
  content: string,
) => Promise<Record<string, string> | undefined>

/** A new skill, written from its name, description, and instructions. */
export function NewSkill({
  write,
  onCreated,
}: {
  write: WriteNew
  onCreated: (name: string) => void
}) {
  const id = useId()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [instructions, setInstructions] = useState("")
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<unknown>()

  const create = async () => {
    setBusy(true)
    setFailure(undefined)
    try {
      const content = `---\nname: ${name}\ndescription: ${description}\n---\n${instructions}\n`
      const refused = await write(name, content)
      if (refused === undefined) onCreated(name)
      else setRefusal(refused)
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {failure !== undefined && <Failure error={failure} />}
      <FieldGroup>
        <Field data-invalid={refusal.name !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
          <Input
            id={`${id}-name`}
            value={name}
            aria-invalid={refusal.name !== undefined || undefined}
            onChange={(event) => setName(event.target.value)}
          />
          {refusal.name !== undefined && <FieldError>{refusal.name}</FieldError>}
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
          <Input
            id={`${id}-description`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <FieldDescription>
            The model sees it in the skill list and reads on from there.
          </FieldDescription>
        </Field>
        <Field data-invalid={refusal.content !== undefined || undefined}>
          <FieldLabel htmlFor={`${id}-instructions`}>Instructions</FieldLabel>
          <Textarea
            id={`${id}-instructions`}
            className="min-h-48"
            value={instructions}
            aria-invalid={refusal.content !== undefined || undefined}
            onChange={(event) => setInstructions(event.target.value)}
          />
          {refusal.content !== undefined && <FieldError>{refusal.content}</FieldError>}
        </Field>
      </FieldGroup>
      <div>
        <Button disabled={busy || name === "" || description === ""} onClick={() => void create()}>
          {busy && <Spinner data-icon="inline-start" />}
          Create
        </Button>
      </div>
    </div>
  )
}
