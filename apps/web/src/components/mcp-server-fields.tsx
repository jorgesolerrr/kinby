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

/** The fields of an MCP server's definition: its entry, then the tools a turn is offered. */
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
  const field = (
    name: Exclude<keyof McpServerDraft, "type" | "readArgs">,
    label: string,
    description: string,
  ) => (
    <Field>
      <FieldLabel htmlFor={`${id}-${name}`}>{label}</FieldLabel>
      {name === "url" || name === "command" ? (
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
          value={[draft.type]}
          disabled={disabled}
          onValueChange={([type]) => {
            if (type === "http" || type === "stdio") onChange({ ...draft, type })
          }}
        >
          <ToggleGroupItem value="http">http</ToggleGroupItem>
          <ToggleGroupItem value="stdio">stdio</ToggleGroupItem>
        </ToggleGroup>
      </FieldSet>
      {draft.type === "http" ? (
        <>
          {field("url", "URL", "A remote server, reached over HTTP.")}
          {field(
            "headers",
            "Headers",
            "One a line, as Name: value. ${VAR} in a value is the instance's secret VAR.",
          )}
        </>
      ) : (
        <>
          {field("command", "Command", "uvx runs the server in the instance's container.")}
          {field("args", "Arguments", "One a line. The first is the package uvx runs.")}
          {field(
            "env",
            "Env",
            "One a line, as NAME=value. ${VAR} in a value is the instance's secret VAR. The server gets no other variable.",
          )}
        </>
      )}
      {field(
        "include",
        "Tools",
        "The tools a turn is offered, one a line. The others are left out.",
      )}
    </FieldGroup>
  )
}
