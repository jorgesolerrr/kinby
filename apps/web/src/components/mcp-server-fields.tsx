import { useId } from "react"

import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { McpServerDraft } from "@/lib/mcp-servers"

/** The fields of an MCP server's definition: its transport, the variables it gets, its tools. */
export function McpServerFields({
  draft,
  disabled,
  onChange,
}: {
  draft: McpServerDraft
  disabled: boolean
  onChange: (draft: McpServerDraft) => void
}) {
  const id = useId()
  const field = (name: keyof McpServerDraft, label: string, description: string) => (
    <Field>
      <FieldLabel htmlFor={`${id}-${name}`}>{label}</FieldLabel>
      {name === "url" || name === "package" ? (
        <Input
          id={`${id}-${name}`}
          value={draft[name]}
          readOnly={disabled}
          onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
        />
      ) : (
        <Textarea
          id={`${id}-${name}`}
          value={draft[name]}
          readOnly={disabled}
          onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
        />
      )}
      <FieldDescription>{description}</FieldDescription>
    </Field>
  )

  return (
    <FieldGroup>
      <FieldSet>
        <FieldLegend variant="label">Transport</FieldLegend>
        <ToggleGroup
          variant="outline"
          value={[draft.transport]}
          disabled={disabled}
          onValueChange={([transport]) => {
            if (transport === "url" || transport === "uvx") onChange({ ...draft, transport })
          }}
        >
          <ToggleGroupItem value="url">URL</ToggleGroupItem>
          <ToggleGroupItem value="uvx">uvx</ToggleGroupItem>
        </ToggleGroup>
      </FieldSet>
      {draft.transport === "url" ? (
        <>
          {field("url", "URL", "A remote server, reached over HTTP.")}
          {field(
            "headers",
            "Headers",
            "One a line, as Name: VARIABLE. Each value is that variable's, from the instance's .env.",
          )}
        </>
      ) : (
        <>
          {field("package", "Package", "uvx runs it in the instance's container.")}
          {field("args", "Arguments", "One a line.")}
        </>
      )}
      {field(
        "env",
        "Variables",
        "The variables a local server gets from the instance's .env, one a line. It gets no others.",
      )}
      {field(
        "include",
        "Tools",
        "The tools a turn is offered, one a line. The others are left out.",
      )}
    </FieldGroup>
  )
}
