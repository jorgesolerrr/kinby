import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { LibraryIcon, SearchIcon, SquarePenIcon } from "lucide-react"
import type * as React from "react"
import { useCallback, useEffect, useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { type Folds, NavInstances, type Reached } from "@/components/nav-instances"
import { NavUser } from "@/components/nav-user"
import { SearchPalette } from "@/components/search-palette"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { useInstanceClient } from "@/hooks/use-instance-client"
import { useOpenFromSidebar } from "@/hooks/use-open-from-sidebar"
import { homePath, LIBRARY_PATH } from "@/lib/selection"

export function AppSidebar({
  instances,
  selected,
  creating,
  libraryOpen,
  client,
  clock,
  selectedClient,
  onSignOut,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  libraryOpen: boolean
  /** Reaches the instances that are not selected, to list their threads and views. */
  client: Pick<Client, "instance">
  clock: Clock
  /** The selected instance's connection, whose thread list the open thread reads and changes. */
  selectedClient: Reached | undefined
  onSignOut: () => void
}) {
  const open = useOpenFromSidebar()
  const [folds, setFolds] = useState<Folds>({})
  const [searching, setSearching] = useState(false)
  const { setOpenMobile } = useSidebar()
  // The palette opens alone, never over the phone's drawer.
  const search = useCallback(() => {
    setSearching(true)
    setOpenMobile(false)
  }, [setOpenMobile])
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "k" || !(event.ctrlKey || event.metaKey)) return
      event.preventDefault()
      search()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [search])
  // Held here, not in the groups, so the phone's drawer closing drops no connection.
  const [reached, setReached] = useState<ReadonlyMap<string, Reached>>(new Map())
  const reach = useCallback((instanceId: string, client: Reached | undefined) => {
    setReached((was) => {
      const next = new Map(was)
      if (client === undefined) next.delete(instanceId)
      else next.set(instanceId, client)
      return next
    })
  }, [])
  const ordered = [
    ...instances.filter((instance) => instance.home),
    ...instances.filter((instance) => !instance.home),
  ]
  return (
    <>
      {ordered.map((instance) => {
        const isSelected = instance === selected
        return (
          <InstanceConnection
            key={instance.instance_id}
            instance={instance}
            isSelected={isSelected}
            client={client}
            selectedClient={isSelected ? selectedClient : undefined}
            onReach={reach}
          />
        )
      })}
      <Sidebar collapsible="offcanvas" {...props}>
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                size="lg"
                onClick={(event) => {
                  event.preventDefault()
                  open(homePath())
                }}
                render={
                  <a href={homePath()}>
                    <span className="flex size-8 shrink-0 items-center justify-center">
                      <KinbyMark className="size-7!" />
                    </span>
                    <span className="font-heading text-lg font-semibold tracking-tight">kinby</span>
                  </a>
                }
              />
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                onClick={(event) => {
                  event.preventDefault()
                  open(homePath())
                }}
                render={
                  <a href={homePath()}>
                    <SquarePenIcon />
                    <span>New thread</span>
                  </a>
                }
              />
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton aria-keyshortcuts="Control+K Meta+K" onClick={search}>
                <SearchIcon />
                <span>Search</span>
                <kbd aria-hidden className="ml-auto font-mono text-xs text-muted-foreground">
                  Ctrl K
                </kbd>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                isActive={libraryOpen}
                aria-current={libraryOpen ? "page" : undefined}
                onClick={(event) => {
                  event.preventDefault()
                  open(LIBRARY_PATH)
                }}
                render={
                  <a href={LIBRARY_PATH}>
                    <LibraryIcon />
                    <span>Library</span>
                  </a>
                }
              />
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <NavInstances
            instances={ordered}
            selected={selected}
            creating={creating}
            clock={clock}
            reached={reached}
            folds={folds}
            onFold={(instanceId, opened) => setFolds((kept) => ({ ...kept, [instanceId]: opened }))}
          />
        </SidebarContent>
        <SidebarFooter>
          <NavUser onSignOut={onSignOut} />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SearchPalette
        open={searching}
        onOpenChange={setSearching}
        clock={clock}
        instances={ordered.flatMap((instance) => {
          const client = reached.get(instance.instance_id)
          return client === undefined ? [] : [{ instance, client }]
        })}
      />
    </>
  )
}

/**
 * Reaches `instance` while it runs, the selected one on the connection the open thread uses and
 * the others on their own, and tells `onReach` which one it is.
 */
function InstanceConnection({
  instance,
  isSelected,
  client,
  selectedClient,
  onReach,
}: {
  instance: InstanceSummary
  isSelected: boolean
  client: Pick<Client, "instance">
  selectedClient: Reached | undefined
  onReach: (instanceId: string, client: Reached | undefined) => void
}) {
  const running = instance.process === "running"
  const ownClient = useInstanceClient(
    client,
    !isSelected && running ? instance.instance_id : undefined,
  )
  const reached = running ? (selectedClient ?? ownClient) : undefined
  useEffect(() => {
    onReach(instance.instance_id, reached)
    return () => onReach(instance.instance_id, undefined)
  }, [onReach, instance.instance_id, reached])
  return null
}
