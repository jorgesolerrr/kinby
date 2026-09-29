import type { OperationState, OperationStep } from "@kinby/contract"
import type * as React from "react"

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
import { CircleCheckIcon, CircleXIcon } from "lucide-react"

const STATES: Record<OperationState, { label: string; icon: React.ReactNode }> = {
  pending: { label: "Waiting", icon: <Spinner /> },
  running: { label: "Running", icon: <Spinner /> },
  succeeded: { label: "Done", icon: <CircleCheckIcon /> },
  failed: { label: "Failed", icon: <CircleXIcon /> },
}

/** A lifecycle operation's steps, in the order the hub ran them, each with how it stands. */
export function OperationSteps({ label, steps }: { label: string; steps: OperationStep[] }) {
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
