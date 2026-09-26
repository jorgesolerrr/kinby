// PROTOTYPE, throwaway. Variant A: tabs for Profile and Knowledge graph; the graph is a timeline
// list with filters on the left and the opened node on the right.
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import type { Memory } from "./flow"
import { AddFact, FilterBar, NodeDetail, NodeList, ProfileEditor, useBrowse } from "./parts"

export const name = "Tabs, timeline and pane"

export function VariantA({ memory }: { memory: Memory }) {
  const browse = useBrowse(memory)
  return (
    <Tabs defaultValue="graph" className="flex min-h-0 flex-1 flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Memory</h1>
        <TabsList>
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="graph">Knowledge graph</TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="profile" className="max-w-3xl">
        <p className="mb-3 text-sm text-muted-foreground">
          What kinby always knows about you. It's in every prompt.
        </p>
        <ProfileEditor memory={memory} rows={16} />
      </TabsContent>
      <TabsContent value="graph" className="flex min-h-0 flex-1 flex-col gap-4">
        <FilterBar browse={browse} />
        <div className="flex min-h-0 flex-1 gap-6">
          <div className="w-1/2 overflow-y-auto">
            <NodeList browse={browse} />
          </div>
          <div className="w-1/2 overflow-y-auto border-l pl-6">
            {browse.selected ? (
              <NodeDetail memory={memory} browse={browse} id={browse.selected} />
            ) : (
              <AddFact memory={memory} browse={browse} />
            )}
          </div>
        </div>
      </TabsContent>
    </Tabs>
  )
}
