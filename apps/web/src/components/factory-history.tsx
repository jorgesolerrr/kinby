import type { Client, Clock, FactoryVersion, FactoryVersionSource } from "@kinby/contract"
import { useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Field, FieldLabel } from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useHubCall } from "@/hooks/use-hub-call"
import { useRead } from "@/hooks/use-read"
import { when } from "@/lib/config-changes"

type Caller = Pick<Client, "call">

const SOURCES: Record<FactoryVersionSource, string> = {
  web: "Saved from the web",
  by_hand: "Changed by hand",
  revert: "Reverted",
  shipped: "kinby's version",
  shipped_update: "kinby's update, merged",
}

/**
 * A factory's versions, newest first: each one's time, source and the steps it changed, its diff,
 * and a revert to it. `onReverted` runs once a revert made a new version.
 */
export function FactoryHistory({
  client,
  clock,
  name,
  onReverted,
}: {
  client: Caller
  clock: Clock
  name: string
  onReverted: () => void
}) {
  const read = useCallback(
    async () => (await client.call("factory.version.list", { name })).versions,
    [client, name],
  )
  const { value: versions, failure, readAgain } = useRead(read, clock)
  const { busy, failure: revertFailure, ask } = useHubCall()
  const revert = (version: string) =>
    ask(async () => {
      await client.call("factory.revert", { name, version })
      readAgain()
      onReverted()
    })

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">History</h2>
      {revertFailure !== undefined && <Failure error={revertFailure} />}
      {versions === undefined ? (
        failure === undefined ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <Failure error={failure} />
        )
      ) : versions.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          kinby&apos;s version as it ships. The first edit keeps it as the first version.
        </p>
      ) : (
        <ol aria-label="History" className="flex flex-col divide-y text-sm">
          {versions.map((version, index) => (
            <li key={version.version}>
              <Version
                client={client}
                name={name}
                version={version}
                current={index === 0}
                busy={busy}
                onRevert={() => void revert(version.version)}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/** One version's row, which unfolds to its diff, read when first unfolded. */
function Version({
  client,
  name,
  version,
  current,
  busy,
  onRevert,
}: {
  client: Caller
  name: string
  version: FactoryVersion
  current: boolean
  busy: boolean
  onRevert: () => void
}) {
  const id = useId()
  const [diff, setDiff] = useState<string>()
  const { failure, ask } = useHubCall()
  const open = (opened: boolean) => {
    if (!opened || diff !== undefined) return
    void ask(async () => {
      setDiff((await client.call("factory.version.diff", { name, version: version.version })).diff)
    })
  }

  return (
    <Collapsible onOpenChange={open}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
        <span className="text-muted-foreground">{when(version.time)}</span>
        <span>{SOURCES[version.source]}</span>
        {version.steps.length > 0 && (
          <span className="min-w-0 truncate font-mono">{version.steps.join(", ")}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <CollapsibleTrigger render={<Button variant="ghost" size="sm" />}>
            Diff
          </CollapsibleTrigger>
          {current ? (
            <Badge variant="secondary">Current</Badge>
          ) : (
            <Button variant="outline" size="sm" disabled={busy} onClick={onRevert}>
              Revert
            </Button>
          )}
        </div>
      </div>
      <CollapsibleContent className="mb-3">
        {failure !== undefined ? (
          <Failure error={failure} />
        ) : diff === undefined ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <Field>
            <FieldLabel htmlFor={id}>Diff</FieldLabel>
            <Textarea id={id} className="min-h-48" value={diff} readOnly />
          </Field>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}
