// PROTOTYPE, throwaway. Variant B: subjects first. Three columns: the profile and every subject
// with its count, the nodes for the chosen subject, the opened node.
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Item, ItemContent, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item"
import { Separator } from "@/components/ui/separator"
import { HashIcon, LayersIcon, UserRoundIcon } from "lucide-react"

import type { Memory } from "./flow"
import { AddFact, FilterBar, NodeDetail, NodeList, ProfileEditor, useBrowse } from "./parts"

export const name = "Subjects first"

export function VariantB({ memory }: { memory: Memory }) {
  const browse = useBrowse(memory)
  const [profile, setProfile] = useState(false)
  const pick = (subject: string | null) => {
    setProfile(false)
    browse.update({ subject })
    browse.select(null)
  }
  return (
    <div className="flex min-h-0 flex-1">
      <nav className="flex w-60 shrink-0 flex-col gap-2 overflow-y-auto border-r p-3">
        <ItemGroup>
          <Item
            size="xs"
            variant={profile ? "muted" : "default"}
            render={<button type="button" onClick={() => setProfile(true)} />}
          >
            <ItemMedia variant="icon">
              <UserRoundIcon />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>Profile</ItemTitle>
            </ItemContent>
          </Item>
          <Separator />
          <Item
            size="xs"
            variant={!profile && !browse.filters.subject ? "muted" : "default"}
            render={<button type="button" onClick={() => pick(null)} />}
          >
            <ItemMedia variant="icon">
              <LayersIcon />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>Everything</ItemTitle>
            </ItemContent>
          </Item>
          {memory.subjects().map(([s, count]) => (
            <Item
              key={s}
              size="xs"
              variant={!profile && browse.filters.subject === s ? "muted" : "default"}
              render={<button type="button" onClick={() => pick(s)} />}
            >
              <ItemMedia variant="icon">
                <HashIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>
                  {s} <Badge variant="outline">{count}</Badge>
                </ItemTitle>
              </ItemContent>
            </Item>
          ))}
        </ItemGroup>
      </nav>
      {profile ? (
        <main className="flex max-w-3xl flex-1 flex-col gap-3 p-6">
          <h1 className="text-xl font-semibold">Profile</h1>
          <ProfileEditor memory={memory} rows={16} />
        </main>
      ) : (
        <>
          <section className="flex w-96 shrink-0 flex-col gap-3 overflow-y-auto border-r p-3">
            <FilterBar browse={browse} compact />
            <NodeList browse={browse} />
          </section>
          <main className="min-w-0 flex-1 overflow-y-auto p-6">
            {browse.selected ? (
              <NodeDetail memory={memory} browse={browse} id={browse.selected} />
            ) : (
              <AddFact memory={memory} browse={browse} />
            )}
          </main>
        </>
      )}
    </div>
  )
}
