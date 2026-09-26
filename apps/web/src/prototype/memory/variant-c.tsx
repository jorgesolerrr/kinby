// PROTOTYPE, throwaway. Variant C: one reading column. The profile sits on top like a document,
// search and the timeline below it, and a node opens in a side sheet.
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { PencilIcon } from "lucide-react"

import type { Memory } from "./flow"
import { AddFact, FilterBar, NodeDetail, NodeList, ProfileEditor, useBrowse } from "./parts"

export const name = "One column, sheet"

export function VariantC({ memory }: { memory: Memory }) {
  const browse = useBrowse(memory)
  const [editing, setEditing] = useState(false)
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 overflow-y-auto p-6">
      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold">What kinby always knows</h1>
          <Button size="sm" variant="ghost" onClick={() => setEditing(!editing)}>
            <PencilIcon /> {editing ? "Done" : "Edit"}
          </Button>
        </div>
        {editing ? (
          <ProfileEditor memory={memory} />
        ) : (
          <pre className="rounded-md bg-muted p-4 text-sm whitespace-pre-wrap">
            {memory.profile.text}
          </pre>
        )}
      </section>
      <Separator />
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">What kinby has learned</h2>
        </div>
        <FilterBar browse={browse} />
        <AddFact memory={memory} browse={browse} />
        <NodeList browse={browse} />
      </section>
      <Sheet
        open={browse.selected !== null}
        onOpenChange={(open) => {
          if (!open) browse.select(null)
        }}
      >
        <SheetContent className="sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Memory</SheetTitle>
          </SheetHeader>
          <div className="px-4">
            {browse.selected && <NodeDetail memory={memory} browse={browse} id={browse.selected} />}
          </div>
        </SheetContent>
      </Sheet>
    </main>
  )
}
