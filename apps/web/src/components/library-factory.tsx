import type { Client, Clock } from "@kinby/contract"
import { useCallback, useId } from "react"
import { parse } from "yaml"

import { Failure } from "@/components/config-alerts"
import { FactoryHistory } from "@/components/factory-history"
import { FactorySteps, type FactoryStep } from "@/components/factory-steps"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"

/** The parts of a `factory.yaml` the summary shows. The hub checked the rest. */
interface FactoryFile {
  steps: FactoryStep[]
}

/**
 * A factory unfolded: its steps, its `factory.yaml` and its other files, read-only, and its
 * History, where it reverts to an earlier version.
 */
export function FactoryEntry({
  client,
  clock,
  name,
}: {
  client: Pick<Client, "call">
  clock: Clock
  name: string
}) {
  const id = useId()
  const read = useCallback(async () => {
    const { "factory.yaml": content = "", ...others } = (await client.call("factory.get", { name }))
      .files
    // The hub reads it with PyYAML, which resolves `<<` merge keys.
    const factory = parse(content, { merge: true }) as FactoryFile
    return { content, factory, files: Object.keys(others) }
  }, [client, name])
  const { value: opened, failure, readAgain } = useRead(read, clock)

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-4">
        <h2 className="text-base font-semibold">What it&apos;s made of</h2>
        {opened === undefined ? (
          failure === undefined ? (
            <Skeleton className="h-72 w-full" />
          ) : (
            <Failure error={failure} />
          )
        ) : (
          <>
            <FactorySteps steps={opened.factory.steps} />
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
      <FactoryHistory client={client} clock={clock} name={name} onReverted={readAgain} />
    </div>
  )
}
