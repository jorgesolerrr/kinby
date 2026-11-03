import type { Client, Clock } from "@kinby/contract"
import { useCallback, useId } from "react"

import { Failure } from "@/components/config-alerts"
import { FactoryInstances } from "@/components/factory-instances"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"

/**
 * A factory unfolded: the instances holding its roles, then its `factory.yaml`, read-only, and its
 * other files.
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
    return { content, files: Object.keys(others) }
  }, [client, name])
  const { value: opened, failure } = useRead(read, clock)

  return (
    <>
      <FactoryInstances client={client} clock={clock} factory={name} />
      {opened === undefined ? (
        failure === undefined ? (
          <Skeleton className="h-72 w-full" />
        ) : (
          <Failure error={failure} />
        )
      ) : (
        <Field>
          <FieldLabel htmlFor={id}>factory.yaml</FieldLabel>
          <Textarea id={id} className="min-h-72" value={opened.content} readOnly />
          {opened.files.length > 0 && (
            <FieldDescription>Other files: {opened.files.join(", ")}</FieldDescription>
          )}
        </Field>
      )}
    </>
  )
}
