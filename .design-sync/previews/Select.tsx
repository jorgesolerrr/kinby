import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@kinby/web"

type Option = { value: string; label: string }

const MODELS: Option[] = [
  { value: "opus", label: "Claude Opus" },
  { value: "sonnet", label: "Claude Sonnet" },
  { value: "haiku", label: "Claude Haiku" },
]

const SHOWN: Option[] = [
  { value: "all", label: "All threads" },
  { value: "mine", label: "Started by me" },
  { value: "routines", label: "From routines" },
]

const SECRETS: Option[] = [
  { value: "typed", label: "Type a value" },
  { value: "SLACK_BOT_TOKEN", label: "SLACK_BOT_TOKEN" },
  { value: "GITHUB_TOKEN", label: "GITHUB_TOKEN" },
]

const Options = ({ items }: { items: Option[] }) => (
  <SelectContent>
    <SelectGroup>
      {items.map((item) => (
        <SelectItem key={item.value} value={item.value}>
          {item.label}
        </SelectItem>
      ))}
    </SelectGroup>
  </SelectContent>
)

export const Default = () => (
  <Select items={MODELS} defaultValue="sonnet">
    <SelectTrigger className="w-48" aria-label="Model">
      <SelectValue />
    </SelectTrigger>
    <Options items={MODELS} />
  </Select>
)

export const Sizes = () => (
  <div className="flex items-center gap-3">
    <Select items={SHOWN} defaultValue="all">
      <SelectTrigger size="sm" className="w-48" aria-label="Show">
        <SelectValue />
      </SelectTrigger>
      <Options items={SHOWN} />
    </Select>
    <Select items={SHOWN} defaultValue="routines">
      <SelectTrigger className="w-48" aria-label="Show">
        <SelectValue />
      </SelectTrigger>
      <Options items={SHOWN} />
    </Select>
  </div>
)

export const Placeholder = () => (
  <Select items={MODELS}>
    <SelectTrigger className="w-48" aria-label="Model">
      <SelectValue placeholder="Pick a model" />
    </SelectTrigger>
    <Options items={MODELS} />
  </Select>
)

export const InField = () => (
  <Field className="w-80">
    <FieldLabel htmlFor="select-secret">Hub secret for GitHub</FieldLabel>
    <Select items={SECRETS} defaultValue="GITHUB_TOKEN">
      <SelectTrigger id="select-secret" className="w-full">
        <SelectValue />
      </SelectTrigger>
      <Options items={SECRETS} />
    </Select>
    <FieldDescription>The instance reads it from the hub at start.</FieldDescription>
  </Field>
)

export const Invalid = () => (
  <Field data-invalid className="w-80">
    <FieldLabel htmlFor="select-secret-bad">Hub secret for Slack</FieldLabel>
    <Select items={SECRETS}>
      <SelectTrigger id="select-secret-bad" className="w-full" aria-invalid>
        <SelectValue placeholder="Pick a secret" />
      </SelectTrigger>
      <Options items={SECRETS} />
    </Select>
    <FieldError>Pick a secret or type a value.</FieldError>
  </Field>
)

export const Disabled = () => (
  <Select items={MODELS} defaultValue="opus" disabled>
    <SelectTrigger className="w-48" aria-label="Model">
      <SelectValue />
    </SelectTrigger>
    <Options items={MODELS} />
  </Select>
)
