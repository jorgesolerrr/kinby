import type { InstanceClient, InstanceSummary } from "@kinby/contract"
import { useSyncExternalStore } from "react"

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { useOpenFromSidebar } from "@/hooks/use-open-from-sidebar"
import { instanceName } from "@/lib/instances"
import { threadPath } from "@/lib/selection"
import { threadList, threadTitle } from "@/lib/thread-list"

type Lister = Pick<InstanceClient, "call">

/**
 * The palette that finds a thread by its title on any of `instances`, each listing the sidebar
 * threads its group keeps on `client`. Picking one opens it and closes the palette.
 */
export function SearchPalette({
  open,
  onOpenChange,
  instances,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  instances: { instance: InstanceSummary; client: Lister }[]
}) {
  const openFromSidebar = useOpenFromSidebar()
  const pick = (path: string) => {
    onOpenChange(false)
    openFromSidebar(path)
  }
  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Search threads"
      description="Find a thread on any running instance by its title."
    >
      <Command filter={byTitle}>
        <CommandInput placeholder="Search threads…" />
        <CommandList>
          <CommandEmpty>No thread matches.</CommandEmpty>
          {instances.map(({ instance, client }) => (
            <InstanceThreads
              key={instance.instance_id}
              instance={instance}
              client={client}
              onPick={pick}
            />
          ))}
        </CommandList>
      </Command>
    </CommandDialog>
  )
}

function InstanceThreads({
  instance,
  client,
  onPick,
}: {
  instance: InstanceSummary
  client: Lister
  onPick: (path: string) => void
}) {
  const list = threadList(client, "sidebar")
  const threads = useSyncExternalStore(list.onChange, list.view)?.threads ?? []
  if (threads.length === 0) return null
  return (
    <CommandGroup heading={instanceName(instance)}>
      {threads.map((thread) => {
        const path = threadPath(instance.instance_id, thread.id)
        return (
          <CommandItem
            key={thread.id}
            value={path}
            keywords={[threadTitle(thread)]}
            onSelect={() => onPick(path)}
          >
            {threadTitle(thread)}
          </CommandItem>
        )
      })}
    </CommandGroup>
  )
}

/** An item shows while its title, its one keyword, holds the search, ignoring case. */
function byTitle(_path: string, search: string, keywords: string[] = []): number {
  const wanted = search.trim().toLocaleLowerCase()
  return keywords.some((title) => title.toLocaleLowerCase().includes(wanted)) ? 1 : 0
}
