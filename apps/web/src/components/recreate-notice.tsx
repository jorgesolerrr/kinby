import type { Client, Clock, RecreateReason } from "@kinby/contract"
import { useCallback, useId, useState } from "react"

import { Progress } from "@/components/operation-progress"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { useFollowing } from "@/hooks/use-following"
import { type Followed, followOperation } from "@/lib/operation"
import { RefreshCwIcon } from "lucide-react"

/** How the notice names each change that waits on a recreate. */
const REASONS: Record<RecreateReason, string> = {
  secrets: "Replaced secrets",
  package_config: "The edited package config",
}

/**
 * Every change that waits on a recreate, and one Recreate button that applies them all and shows
 * its steps. Nothing shows while nothing waits. `onRecreated` hears the recreate end, and must keep
 * its identity.
 */
export function RecreateNotice({
  caller,
  clock,
  instanceId,
  reasons,
  onRecreated,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instanceId: string
  reasons: RecreateReason[]
  onRecreated: () => void
}) {
  const titleId = useId()
  // A new object for each click, so recreating again follows a new operation.
  const [request, setRequest] = useState<object>()
  const follow = useCallback(
    (_asked: object, report: (followed: Followed) => void) =>
      followOperation(
        caller,
        () => caller.call("instance.recreate", { instance_id: instanceId }),
        (followed) => {
          report(followed)
          if (followed.state === "succeeded") onRecreated()
        },
        clock,
      ),
    [caller, clock, instanceId, onRecreated],
  )
  const followed = useFollowing(request, follow)
  // A recreate that ended leaves the button for the next reason.
  const busy = request !== undefined && (followed === undefined || followed.state === "running")
  if (reasons.length === 0) return null
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3">
      <Alert>
        <RefreshCwIcon />
        <AlertTitle id={titleId}>Recreate to apply</AlertTitle>
        <AlertDescription>
          <p>The instance applies these once its container is recreated:</p>
          <ul className="list-disc pl-5">
            {reasons.map((reason) => (
              <li key={reason}>{REASONS[reason]}</li>
            ))}
          </ul>
        </AlertDescription>
        <AlertAction>
          <Button size="sm" disabled={busy} onClick={() => setRequest({})}>
            {busy && <Spinner data-icon="inline-start" />}
            Recreate
          </Button>
        </AlertAction>
      </Alert>
      {followed !== undefined && followed.state !== "succeeded" && (
        <Progress label="Recreate steps" failure="The recreate failed" operation={followed} />
      )}
    </section>
  )
}
