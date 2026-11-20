import type { Client, Clock } from "@kinby/contract"
import { useCallback, useId } from "react"
import { parse } from "yaml"

import { Failure } from "@/components/config-alerts"
import { FactoryHistory } from "@/components/factory-history"
import { FactorySteps } from "@/components/factory-steps"
import type { FactoryStep } from "@/lib/factory-steps"
import { Badge } from "@/components/ui/badge"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"
import type { StepLink } from "@/lib/selection"

/** The parts of a `factory.yaml` the summary shows. The hub checked the rest. */
interface FactoryFile {
  requires?: { commands?: string[] }
  steps: FactoryStep[]
}

/**
 * A factory unfolded: its steps, its `factory.yaml` and its other files, read-only, and its
 * History, where it reverts to an earlier version. A shipped factory the user changed shows that
 * it differs from kinby's, and whether kinby's update waits for the user. Opened `from` a run, it
 * opens on the card of the run's step.
 */
export function FactoryEntry({
  client,
  clock,
  name,
  from,
}: {
  client: Pick<Client, "call">
  clock: Clock
  name: string
  from?: StepLink
}) {
  const id = useId()
  const runId = from?.runId
  const read = useCallback(async () => {
    const [result, run] = await Promise.all([
      client.call("factory.get", { name }),
      runId === undefined ? undefined : client.call("factory.run.get", { run_id: runId }),
    ])
    const { "factory.yaml": content = "", ...others } = result.files
    // The hub reads it with PyYAML, which resolves `<<` merge keys.
    const factory = parse(content, { merge: true }) as FactoryFile
    return { result, run, content, factory, files: Object.keys(others) }
  }, [client, name, runId])
  const { value: opened, failure, readAgain } = useRead(read, clock)

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">What it&apos;s made of</h2>
          {opened?.result.differs_from_shipped && (
            <Badge variant="secondary">Differs from shipped</Badge>
          )}
          {opened?.result.shipped_update_waiting && <Badge>Update waiting</Badge>}
        </div>
        {opened === undefined ? (
          failure === undefined ? (
            <Skeleton className="h-72 w-full" />
          ) : (
            <Failure error={failure} />
          )
        ) : (
          <>
            <FactorySteps
              steps={opened.factory.steps}
              commands={opened.factory.requires?.commands ?? []}
              saving={{ client, factory: opened.result }}
              from={from && opened.run && { step: from.step, run: opened.run }}
              onSaved={readAgain}
            />
            <Field>
              <FieldLabel htmlFor={id}>factory.yaml</FieldLabel>
              <Textarea id={id} className="min-h-72" value={opened.content} readOnly />
              {opened.files.length > 0 && (
                <FieldDescription>Other files: {opened.files.join(", ")}</FieldDescription>
              )}
            </Field>
          </>
        )}
      </section>
      <FactoryHistory
        client={client}
        clock={clock}
        name={name}
        current={opened?.result.version}
        onReverted={readAgain}
      />
    </div>
  )
}
