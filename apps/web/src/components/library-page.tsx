import type { Client, Clock, LibraryEntry, LibraryKind } from "@kinby/contract"
import { useCallback, useId, useState } from "react"

import { Failure } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useRead } from "@/hooks/use-read"

type Caller = Pick<Client, "call">

const KINDS: { kind: LibraryKind; title: string }[] = [
  { kind: "skill", title: "Skills" },
  { kind: "factory", title: "Factories" },
]

/** The hub's library: the skills and factories an instance can pick. Any entry opens to read. */
export function LibraryPage({ client, clock }: { client: Caller; clock: Clock }) {
  const list = useCallback(() => client.call("library.list", {}), [client])
  const { value: listed, failure } = useRead(list, clock)
  const [opened, setOpened] = useState<LibraryEntry>()

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-xl font-semibold">Library</h1>
      {failure !== undefined && <Failure error={failure} />}
      {listed === undefined
        ? failure === undefined && <Skeleton className="h-72 w-full max-w-2xl" />
        : KINDS.map(({ kind, title }) => (
            <section key={kind} className="flex max-w-2xl flex-col gap-2">
              <h2 className="font-medium">{title}</h2>
              <ItemGroup aria-label={title}>
                {listed.entries
                  .filter((entry) => entry.kind === kind)
                  .map((entry) => (
                    <EntryItem
                      key={entry.name}
                      entry={entry}
                      selected={opened?.kind === kind && opened.name === entry.name}
                      onSelect={() => setOpened(entry)}
                    />
                  ))}
              </ItemGroup>
            </section>
          ))}
      {opened !== undefined && (
        <>
          <Separator className="max-w-2xl" />
          <EntryView
            key={`${opened.kind} ${opened.name}`}
            client={client}
            clock={clock}
            entry={opened}
          />
        </>
      )}
    </div>
  )
}

function EntryItem({
  entry,
  selected,
  onSelect,
}: {
  entry: LibraryEntry
  selected: boolean
  onSelect: () => void
}) {
  return (
    <Item
      size="sm"
      variant={selected ? "outline" : "default"}
      className="text-left"
      render={
        <button
          type="button"
          aria-label={`${entry.name}, ${entry.source} ${entry.kind}`}
          aria-current={selected || undefined}
          onClick={onSelect}
        />
      }
    >
      <ItemContent>
        <ItemTitle>
          {entry.name}
          <Badge variant="secondary">{entry.source === "shipped" ? "Shipped" : "Written"}</Badge>
        </ItemTitle>
        {entry.description !== null && <ItemDescription>{entry.description}</ItemDescription>}
      </ItemContent>
    </Item>
  )
}

/** The entry's main file, a skill's `SKILL.md` or a factory's `factory.yaml`, and its others. */
interface Opened {
  file: string
  content: string
  files: string[]
}

async function openEntry(client: Caller, entry: LibraryEntry): Promise<Opened> {
  if (entry.kind === "skill") {
    const skill = await client.call("library.skill.read", { name: entry.name })
    return { file: "SKILL.md", content: skill.content, files: skill.files }
  }
  const { "factory.yaml": content = "", ...others } = (
    await client.call("factory.get", { name: entry.name })
  ).files
  return { file: "factory.yaml", content, files: Object.keys(others) }
}

function EntryView({
  client,
  clock,
  entry,
}: {
  client: Caller
  clock: Clock
  entry: LibraryEntry
}) {
  const id = useId()
  const read = useCallback(() => openEntry(client, entry), [client, entry])
  const { value: opened, failure } = useRead(read, clock)

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full max-w-2xl" />
    ) : (
      <Failure error={failure} />
    )
  }
  return (
    <Field className="max-w-2xl">
      <FieldLabel htmlFor={id}>{opened.file}</FieldLabel>
      <Textarea id={id} className="min-h-72" value={opened.content} readOnly />
      {opened.files.length > 0 && (
        <FieldDescription>Other files: {opened.files.join(", ")}</FieldDescription>
      )}
    </Field>
  )
}
