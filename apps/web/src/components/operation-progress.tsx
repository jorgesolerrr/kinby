import type { OperationState, OperationStep } from "@kinby/contract"
import { CircleCheckIcon, CircleXIcon } from "lucide-react"
import type * as React from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"

const STATES: Record<OperationState, { label: string; icon: React.ReactNode }> = {
  pending: { label: "Waiting", icon: <Spinner /> },
  running: { label: "Running", icon: <Spinner /> },
  succeeded: { label: "Done", icon: <CircleCheckIcon /> },
  failed: { label: "Failed", icon: <CircleXIcon /> },
}

/** An operation's steps, and why it failed when no step says so. */
export function Progress({
  label,
  failure,
  operation,
}: {
  label: string
  failure: string
  operation: { state: string; steps: OperationStep[]; detail?: string }
}) {
  const failedOutsideAStep =
    operation.state === "failed" && !operation.steps.some((step) => step.state === "failed")
  return (
    <>
      <Steps label={label} steps={operation.steps} />
      {failedOutsideAStep && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>{failure}</AlertTitle>
          <AlertDescription>{operation.detail}</AlertDescription>
        </Alert>
      )}
    </>
  )
}

function Steps({ label, steps }: { label: string; steps: OperationStep[] }) {
  return (
    <ItemGroup aria-label={label}>
      {steps.map((step) => (
        <Item key={step.name} render={<li />} aria-label={step.name} variant="outline" size="sm">
          <ItemMedia variant="icon">{STATES[step.state].icon}</ItemMedia>
          <ItemContent>
            <ItemTitle>{step.name}</ItemTitle>
            <ItemDescription>{step.detail}</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant={step.state === "failed" ? "destructive" : "secondary"}>
              {STATES[step.state].label}
            </Badge>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}
