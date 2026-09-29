import type { OperationStep } from "@kinby/contract"
import { CircleXIcon } from "lucide-react"

import { OperationSteps } from "@/components/operation-steps"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

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
      <OperationSteps label={label} steps={operation.steps} />
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
