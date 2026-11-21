import type { Client, FactoryVersion } from "@kinby/contract"
import { useEffect, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Field, FieldLabel } from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useHubCall } from "@/hooks/use-hub-call"
import { VERSION_SOURCES, when } from "@/lib/config-changes"

type Caller = Pick<Client, "call">

/**
 * A factory's `versions`, newest first, or why they failed to read: each one's time, source and
 * the steps it changed, its diff, and a revert to it. `onReverted` runs once a revert made a new
 * version. `rowId` names each version's row, and `shown` asks for one: History scrolls to it and
 * unfolds its diff, each time it is asked anew.
 */
export function FactoryHistory({
  client,
  name,
  versions,
  failure,
  rowId,
  shown,
  onReverted,
}: {
  client: Caller
  name: string
  versions: FactoryVersion[] | undefined
  failure: unknown
  rowId: (version: string) => string
  shown?: { version: string }
  onReverted: () => void
}) {
  const { busy, failure: revertFailure, ask } = useHubCall()
  const revert = (version: string) =>
    ask(async () => {
      await client.call("factory.revert", { name, version })
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
            <li key={version.version} id={rowId(version.version)} className="scroll-mt-24">
              <Version
                client={client}
                name={name}
                version={version}
                current={index === 0}
                busy={busy}
                rowId={rowId(version.version)}
                shown={shown?.version === version.version ? shown : undefined}
                onRevert={() => void revert(version.version)}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/** One version's row, which unfolds to its diff, read when first unfolded, and unfolds and
 * scrolls into view when `shown`. */
function Version({
  client,
  name,
  version,
  current,
  busy,
  rowId,
  shown,
  onRevert,
}: {
  client: Caller
  name: string
  version: FactoryVersion
  current: boolean
  busy: boolean
  rowId: string
  shown?: { version: string }
  onRevert: () => void
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [wasShown, setWasShown] = useState(shown)
  if (shown !== wasShown) {
    setWasShown(shown)
    if (shown !== undefined) setOpen(true)
  }
  useEffect(() => {
    if (shown !== undefined) {
      document.getElementById(rowId)?.scrollIntoView({ behavior: "smooth", block: "start" })
    }
  }, [shown, rowId])
  const [diff, setDiff] = useState<string>()
  const [failure, setFailure] = useState<unknown>()
  useEffect(() => {
    if (!open || diff !== undefined) return
    client.call("factory.version.diff", { name, version: version.version }).then(
      (read) => setDiff(read.diff),
      (error: unknown) => setFailure(error),
    )
  }, [open, diff, client, name, version.version])
  const unfold = (opened: boolean) => {
    setOpen(opened)
    setFailure(undefined)
  }

  return (
    <Collapsible open={open} onOpenChange={unfold}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
        <span className="text-muted-foreground">{when(version.time)}</span>
        <span>{VERSION_SOURCES[version.source]}</span>
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
