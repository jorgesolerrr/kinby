import { useId } from "react"

import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { type McpServerDraft, type McpServerField, TOOL_STATES } from "@/lib/mcp-servers"

/**
 * The fields of an MCP server's definition: its entry, then its tools' states. Each shows the
 * hub's reason, in `refused`, for refusing its value.
 */
export function McpServerFields({
  draft,
  refused,
  disabled,
  onChange,
}: {
  draft: McpServerDraft
  refused: Partial<Record<Exclude<McpServerField, "name">, string>>
  disabled: boolean
  onChange: (draft: McpServerDraft) => void
}) {
  const id = useId()
  const field = (name: Exclude<McpServerField, "name">, label: string, description: string) => {
    const props = {
      id: `${id}-${name}`,
      value: draft[name],
      readOnly: disabled,
      "aria-invalid": refused[name] !== undefined || undefined,
      "aria-describedby": refused[name] === undefined ? undefined : `${id}-${name}-error`,
    }
    return (
      <Field data-invalid={refused[name] !== undefined || undefined}>
        <FieldLabel htmlFor={`${id}-${name}`}>{label}</FieldLabel>
        {name === "url" || name === "command" ? (
          <Input
            {...props}
            onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
          />
        ) : (
          <Textarea
            {...props}
            onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
          />
        )}
        <FieldDescription>{description}</FieldDescription>
        {refused[name] !== undefined && (
          <FieldError id={`${id}-${name}-error`}>{refused[name]}</FieldError>
        )}
      </Field>
    )
  }

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
          {field("command", "Command", "uvx or npx runs the server in the instance's container.")}
          {field("args", "Arguments", "One a line. The first is the package the command runs.")}
          {field(
            "env",
            "Env",
            "One a line, as NAME=value. ${VAR} in a value is the instance's secret VAR. The server gets no other variable.",
          )}
        </>
      )}
      <FieldSet>
        <FieldLegend variant="label">Default state</FieldLegend>
        <ToggleGroup
          variant="outline"
          value={[draft.defaultState]}
          disabled={disabled}
          onValueChange={([state]) => {
            const chosen = TOOL_STATES.find((each) => each === state)
            if (chosen) onChange({ ...draft, defaultState: chosen })
          }}
        >
          {TOOL_STATES.map((state) => (
            <ToggleGroupItem key={state} value={state}>
              {state}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <FieldDescription>
          The state of each tool not named below, one the server adds later too. A loaded tool is
          sent to the model on every call. A searchable tool reaches no turn until kinby has tool
          search. An off tool is left out.
        </FieldDescription>
      </FieldSet>
      {field("loaded", "Loaded tools", "One a line.")}
      {field("searchable", "Searchable tools", "One a line.")}
      {field("off", "Off tools", "One a line.")}
    </FieldGroup>
  )
}
