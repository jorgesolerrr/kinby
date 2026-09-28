import type { InstanceSummary } from "@kinby/contract"
import type * as React from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { instanceName, observedState } from "@/lib/instances"
import { CREATE_PATH, instancePath, openCreateWizard, selectInstance } from "@/lib/selection"
import { CircleAlertIcon, PlusIcon } from "lucide-react"

export function NavInstances({
  instances,
  selected,
  creating,
  threads,
}: {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
  creating: boolean
  /** Shown in the selected instance's row, in place of its state. */
  threads: React.ReactNode
}) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Instances</SidebarGroupLabel>
      <SidebarMenu>
        {instances.map((instance) => {
          const name = instanceName(instance)
          const isSelected = instance === selected
          return (
            <SidebarMenuItem key={instance.instance_id}>
              <SidebarMenuButton
                size="lg"
                tooltip={name}
                isActive={isSelected}
                aria-current={isSelected ? "page" : undefined}
                onClick={(event) => {
                  event.preventDefault()
                  selectInstance(instance.instance_id)
                }}
                render={
                  <a href={instancePath(instance.instance_id)}>
                    <InstanceAvatar avatar={instance.avatar} name={name} />
                    <span>{name}</span>
                  </a>
                }
              />
              {isSelected && threads ? (
                threads
              ) : (
                <SidebarMenuBadge>
                  <span className="flex items-center gap-1">
                    {instance.setup_pending && (
                      <>
                        <CircleAlertIcon aria-hidden className="size-3.5" />
                        <span className="sr-only">Setup pending</span>
                      </>
                    )}
                    {observedState(instance)}
                  </span>
                </SidebarMenuBadge>
              )}
            </SidebarMenuItem>
          )
        })}
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip="New instance"
            isActive={creating}
            aria-current={creating ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault()
              openCreateWizard()
            }}
            render={
              <a href={CREATE_PATH}>
                <PlusIcon />
                <span>New instance</span>
              </a>
            }
          />
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  )
}
