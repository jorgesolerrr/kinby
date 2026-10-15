import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// No hub secret has an empty name, so it stands for typing a value.
const TYPED = ""

/** Pick a hub secret by name for the secret field *label*, or typing a value: `undefined`. */
export function HubSecretPicker({
  id,
  label,
  kept,
  picked,
  invalid,
  onPick,
}: {
  id: string
  label: string
  kept: string[]
  picked: string | undefined
  invalid?: true
  onPick: (picked: string | undefined) => void
}) {
  const items = [
    { value: TYPED, label: "Type a value" },
    ...kept.map((name) => ({ value: name, label: name })),
  ]
  return (
    <Select
      items={items}
      value={picked ?? TYPED}
      onValueChange={(next) => onPick(next || undefined)}
    >
      <SelectTrigger id={id} aria-label={`Hub secret for ${label}`} aria-invalid={invalid}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
