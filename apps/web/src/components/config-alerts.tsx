import { CallError } from "@kinby/contract"
import type { Warning } from "@kinby/contract"
import { CircleXIcon, TriangleAlertIcon } from "lucide-react"
import { use } from "react"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { OlderCore } from "@/lib/older-core"
import { lost, reason } from "@/lib/operation"

/**
 * A call the instance did not answer, or one it refused, with its reason. A method an older core
 * lacks points to the update instead.
 */
export function Failure({ error }: { error: unknown }) {
  const openUpdate = use(OlderCore)
  const refused = error instanceof CallError && !lost(error)
  if (refused && error.code === "NOT_FOUND" && openUpdate !== undefined) {
    return (
      <Alert>
        <TriangleAlertIcon />
        <AlertTitle>This instance runs an older core</AlertTitle>
        <AlertDescription>
          Its core does not have this section yet. Update it from Package and version.
        </AlertDescription>
        <AlertAction>
          <Button size="sm" variant="outline" onClick={openUpdate}>
            Open Package and version
          </Button>
        </AlertAction>
      </Alert>
    )
  }
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>{refused ? "The instance refused it" : "The instance did not answer"}</AlertTitle>
      <AlertDescription>{reason(error)}</AlertDescription>
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
