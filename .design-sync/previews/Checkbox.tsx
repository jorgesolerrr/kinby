import {
  Checkbox,
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@kinby/web"

const SKILLS = [
  { name: "morning-brief", description: "Plans your day from calendar and inbox", checked: true },
  { name: "pay-invoices", description: "Finds invoices due this week", checked: true },
  { name: "repository-survey", description: "Maps a codebase before a change", checked: false },
]

export const States = () => (
  <div className="flex items-center gap-4">
    <Checkbox aria-label="Unchecked" />
    <Checkbox aria-label="Checked" defaultChecked />
    <Field orientation="horizontal" className="w-fit">
      <Checkbox aria-label="Disabled" disabled />
    </Field>
    <Field orientation="horizontal" className="w-fit">
      <Checkbox aria-label="Disabled checked" disabled defaultChecked />
    </Field>
    <Checkbox aria-label="Invalid" aria-invalid />
  </div>
)

export const WithLabel = () => (
  <Field orientation="horizontal">
    <Checkbox id="cb-recreate" defaultChecked />
    <FieldLabel htmlFor="cb-recreate">Recreate the running ones now</FieldLabel>
  </Field>
)

export const SkillPicker = () => (
  <FieldGroup className="w-80 gap-3">
    {SKILLS.map((skill) => (
      <Field key={skill.name} orientation="horizontal">
        <Checkbox id={`cb-${skill.name}`} defaultChecked={skill.checked} />
        <FieldContent>
          <FieldLabel htmlFor={`cb-${skill.name}`}>{skill.name}</FieldLabel>
          <FieldDescription>{skill.description}</FieldDescription>
        </FieldContent>
      </Field>
    ))}
  </FieldGroup>
)
