import type { InstanceSummary } from "@kinby/contract"

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { instanceName } from "@/lib/instances"
import { instancePath, selectInstance } from "@/lib/selection"
import { BotIcon } from "lucide-react"

export function NavInstances({
  instances,
  selected,
}: {
  instances: InstanceSummary[]
  selected: InstanceSummary | undefined
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
                tooltip={name}
                isActive={isSelected}
                aria-current={isSelected ? "page" : undefined}
                onClick={(event) => {
                  event.preventDefault()
                  selectInstance(instance.instance_id)
                }}
                render={
                  <a href={instancePath(instance.instance_id)}>
                    <BotIcon />
                    <span>{name}</span>
                  </a>
                }
              />
              <SidebarMenuBadge>{instance.intended_state}</SidebarMenuBadge>
            </SidebarMenuItem>
          )
        })}
      </SidebarMenu>
    </SidebarGroup>
  )
}
