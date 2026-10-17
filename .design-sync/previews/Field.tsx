import {
  Button,
  Checkbox,
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSeparator,
  FieldSet,
  Input,
  Switch,
  Textarea,
} from "@kinby/web"

export const Default = () => (
  <Field className="w-80">
    <FieldLabel htmlFor="field-description">Description</FieldLabel>
    <Input id="field-description" defaultValue="Plans the day from calendar and inbox" />
    <FieldDescription>The model sees it in the skill list and reads on from there.</FieldDescription>
  </Field>
)

export const WithError = () => (
  <Field data-invalid className="w-80">
    <FieldLabel htmlFor="field-name">Name</FieldLabel>
    <Input id="field-name" aria-invalid defaultValue="Morning Brief!" />
    <FieldError>Use lowercase letters, digits and hyphens.</FieldError>
  </Field>
)

export const NewSkill = () => (
  <FieldGroup className="w-[28rem]">
    <Field>
      <FieldLabel htmlFor="field-skill-name">Name</FieldLabel>
      <Input id="field-skill-name" defaultValue="pay-invoices" />
    </Field>
    <Field>
      <FieldLabel htmlFor="field-skill-instructions">Instructions</FieldLabel>
      <Textarea
        id="field-skill-instructions"
        defaultValue="Find invoices due this week in my inbox, list them with amount and due date, and ask before paying any."
      />
    </Field>
    <div>
      <Button>Create skill</Button>
    </div>
  </FieldGroup>
)

export const Horizontal = () => (
  <FieldSet className="w-[28rem]">
    <FieldLegend>Notifications</FieldLegend>
    <FieldDescription>When kinby reaches out without being asked.</FieldDescription>
    <FieldGroup>
      <Field orientation="horizontal">
        <FieldContent>
          <FieldLabel htmlFor="field-n-routines">Routine results</FieldLabel>
          <FieldDescription>A message when a routine finishes.</FieldDescription>
        </FieldContent>
        <Switch id="field-n-routines" defaultChecked />
      </Field>
      <FieldSeparator />
      <Field orientation="horizontal">
        <Checkbox id="field-n-usage" />
        <FieldLabel htmlFor="field-n-usage">Warn me at 80% of the monthly budget</FieldLabel>
      </Field>
    </FieldGroup>
  </FieldSet>
)

export const Disabled = () => (
  <Field data-disabled className="w-80">
    <FieldLabel htmlFor="field-hub">Hub address</FieldLabel>
    <Input id="field-hub" disabled defaultValue="https://hub.kinby.local" />
    <FieldDescription>Set by the hub when this instance was created.</FieldDescription>
  </Field>
)
