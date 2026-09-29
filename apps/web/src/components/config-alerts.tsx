import type { Warning } from "@kinby/contract"
import { CircleXIcon, TriangleAlertIcon } from "lucide-react"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"

/** A call the instance did not answer, or refused, with its reason. */
export function Failure({
  title = "The instance did not answer",
  children,
}: {
  title?: string
  children: string
}) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}

/** A save refused because `file` changed since it was read. "Load theirs" reads it again. */
export function StaleAlert({ file, onLoad }: { file: string; onLoad: () => void }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>Changed since you opened it</AlertTitle>
      <AlertDescription>
        {file} was saved by someone else, most likely the agent. Load theirs to see it. Your edits
        here are dropped.
      </AlertDescription>
      <AlertAction>
        <Button size="sm" variant="outline" onClick={onLoad}>
          Load theirs
        </Button>
      </AlertAction>
    </Alert>
  )
}

/** What the instance could not load, so the list leaves it out. */
export function Warnings({ warnings }: { warnings: readonly Warning[] }) {
  if (warnings.length === 0) return null
  return (
    <Alert>
      <TriangleAlertIcon />
      <AlertTitle>Some files did not load</AlertTitle>
      <AlertDescription>
        <ul>
          {warnings.map((warning) => (
            <li key={`${warning.sources.join(" ")} ${warning.message}`}>
              {warning.sources.join(", ")}: {warning.message}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  )
}
