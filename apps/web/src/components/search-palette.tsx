import type { Clock, InstanceSummary } from "@kinby/contract"

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import type { Reached } from "@/components/nav-instances"
import { useOpenFromSidebar } from "@/hooks/use-open-from-sidebar"
import { useSidebarThreads } from "@/hooks/use-sidebar-threads"
import { instanceName } from "@/lib/instances"
import { threadPath } from "@/lib/selection"
import { threadTitle } from "@/lib/thread-list"

/**
 * The palette that finds a thread by its title on any of `instances`, each listing its sidebar
 * threads on `client` while the palette is open. Picking one opens it and closes the palette.
 */
export function SearchPalette({
  open,
  onOpenChange,
  clock,
  instances,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  clock: Clock
  instances: { instance: InstanceSummary; client: Reached }[]
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
              clock={clock}
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
  clock,
  onPick,
}: {
  instance: InstanceSummary
  client: Reached
  clock: Clock
  onPick: (path: string) => void
}) {
  const threads = useSidebarThreads(client, clock) ?? []
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
