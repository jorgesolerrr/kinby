import type { Client, Clock } from "@kinby/contract"
import { ArrowRightIcon } from "lucide-react"
import { useCallback, useId } from "react"
import { parse } from "yaml"

import { Failure } from "@/components/config-alerts"
import { FactoryInstances } from "@/components/factory-instances"
import { Badge } from "@/components/ui/badge"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"

/** The parts of a `factory.yaml` the summary shows. The hub checked the rest. */
interface FactoryFile {
  instances: Record<
    string,
    { image?: string; suggested_picks?: string[]; setup_fields?: { label: string }[] }
  >
  steps: { id: string }[]
}

/**
 * A factory unfolded: the instances holding its roles, then what it is made of, read-only: its
 * roles, its steps, its `factory.yaml` and its other files.
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
  const { value: opened, failure } = useRead(read, clock)

  return (
    <>
      <FactoryInstances client={client} clock={clock} factory={name} />
      <section className="flex max-w-2xl flex-col gap-4">
        <h2 className="text-base font-semibold">What it&apos;s made of</h2>
        {opened === undefined ? (
          failure === undefined ? (
            <Skeleton className="h-72 w-full" />
          ) : (
            <Failure error={failure} />
          )
        ) : (
          <>
            {Object.entries(opened.factory.instances).map(
              ([role, { image, suggested_picks: picks = [], setup_fields: fields = [] }]) => (
                <section key={role} aria-label={`${role} role`}>
                  <dl className="flex flex-col gap-1 text-sm">
                    <div className="flex gap-2">
                      <dt className="w-32 shrink-0 text-muted-foreground">Role</dt>
                      <dd className="font-mono">
                        {role}{" "}
                        <span className="text-muted-foreground">
                          · {image === undefined ? "kinby's base image" : `image ${image}`}
                        </span>
                      </dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="w-32 shrink-0 text-muted-foreground">Suggested picks</dt>
                      <dd className="flex flex-wrap gap-1">
                        {picks.length === 0
                          ? "None"
                          : picks.map((pick) => (
                              <Badge key={pick} variant="outline">
                                {pick}
                              </Badge>
                            ))}
                      </dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="w-32 shrink-0 text-muted-foreground">Asks for</dt>
                      <dd>{fields.map((field) => field.label).join(", ") || "None"}</dd>
                    </div>
                  </dl>
                </section>
              ),
            )}
            <dl className="flex gap-2 text-sm">
              <dt className="w-32 shrink-0 text-muted-foreground">Steps</dt>
              <dd>
                <ol
                  aria-label="Steps"
                  className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono"
                >
                  {opened.factory.steps.map((step, index) => (
                    <li key={step.id} className="flex items-center gap-2">
                      {index > 0 && <ArrowRightIcon className="size-3.5 text-muted-foreground" />}
                      {step.id}
                    </li>
                  ))}
                </ol>
              </dd>
            </dl>
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
    </>
  )
}
