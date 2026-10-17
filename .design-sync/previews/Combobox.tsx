import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@kinby/web"

const TIMEZONES = [
  "Europe/Madrid",
  "Europe/London",
  "America/New_York",
  "America/Havana",
  "Asia/Tokyo",
]

const Options = () => (
  <ComboboxContent>
    <ComboboxEmpty>No timezone matches.</ComboboxEmpty>
    <ComboboxList>
      {(item: string) => (
        <ComboboxItem key={item} value={item}>
          {item}
        </ComboboxItem>
      )}
    </ComboboxList>
  </ComboboxContent>
)

export const Default = () => (
  <Combobox items={TIMEZONES} defaultValue="Europe/Madrid">
    <ComboboxInput className="w-64" aria-label="Timezone" placeholder="Search a timezone" />
    <Options />
  </Combobox>
)

export const Placeholder = () => (
  <Combobox items={TIMEZONES}>
    <ComboboxInput className="w-64" aria-label="Timezone" placeholder="Search a timezone" />
    <Options />
  </Combobox>
)

export const WithClear = () => (
  <Combobox items={TIMEZONES} defaultValue="America/New_York">
    <ComboboxInput className="w-64" aria-label="Timezone" showClear />
    <Options />
  </Combobox>
)

export const InField = () => (
  <Field className="w-80">
    <FieldLabel htmlFor="combobox-tz">Timezone</FieldLabel>
    <Combobox items={TIMEZONES} defaultValue="Europe/Madrid">
      <ComboboxInput id="combobox-tz" placeholder="Search a timezone" />
      <Options />
    </Combobox>
    <FieldDescription>Routine schedules read in this zone.</FieldDescription>
  </Field>
)

export const Invalid = () => (
  <Field data-invalid className="w-80">
    <FieldLabel htmlFor="combobox-tz-bad">Timezone</FieldLabel>
    <Combobox items={TIMEZONES}>
      <ComboboxInput id="combobox-tz-bad" aria-invalid placeholder="Search a timezone" />
      <Options />
    </Combobox>
    <FieldError>Pick a timezone from the list.</FieldError>
  </Field>
)

export const Disabled = () => (
  <Combobox items={TIMEZONES} defaultValue="Asia/Tokyo" disabled>
    <ComboboxInput className="w-64" aria-label="Timezone" disabled />
    <Options />
  </Combobox>
)
