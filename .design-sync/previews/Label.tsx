import { Checkbox, Field, Input, Label } from "@kinby/web"

export const Default = () => (
  <div className="flex w-80 flex-col gap-2">
    <Label htmlFor="label-instance">Instance name</Label>
    <Input id="label-instance" defaultValue="research-assistant" />
  </div>
)

export const WithCheckbox = () => (
  <div className="flex items-center gap-2">
    <Checkbox id="label-save" defaultChecked />
    <Label htmlFor="label-save">Save to the hub</Label>
  </div>
)

export const Disabled = () => (
  <div className="group" data-disabled="true">
    <Field orientation="horizontal">
      <Checkbox id="label-recreate" disabled />
      <Label htmlFor="label-recreate">Recreate the running ones now</Label>
    </Field>
  </div>
)
