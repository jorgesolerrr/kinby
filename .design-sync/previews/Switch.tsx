import { Field, FieldContent, FieldDescription, FieldLabel, Switch } from "@kinby/web"

export const States = () => (
  <div className="flex items-center gap-4">
    <Switch aria-label="Off" />
    <Switch aria-label="On" defaultChecked />
    <Switch aria-label="Disabled" disabled />
    <Switch aria-label="Disabled on" disabled defaultChecked />
  </div>
)

export const Sizes = () => (
  <div className="flex items-center gap-4">
    <Switch size="sm" aria-label="Small" defaultChecked />
    <Switch aria-label="Default" defaultChecked />
  </div>
)

export const WithLabel = () => (
  <Field orientation="horizontal" className="w-80">
    <FieldContent>
      <FieldLabel htmlFor="sw-brief">Morning brief</FieldLabel>
      <FieldDescription>Runs every weekday at 8:00</FieldDescription>
    </FieldContent>
    <Switch id="sw-brief" defaultChecked />
  </Field>
)
