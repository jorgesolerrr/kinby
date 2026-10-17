import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarProvider,
} from "@kinby/web"

export const LoadingThreads = () => (
  <SidebarProvider className="min-h-0 w-64 rounded-xl bg-sidebar">
    <SidebarGroup>
      <SidebarGroupLabel>Threads</SidebarGroupLabel>
      <SidebarMenu>
        {[0, 1, 2, 3].map((index) => (
          <SidebarMenuItem key={index}>
            <SidebarMenuSkeleton showIcon={index % 2 === 0} />
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  </SidebarProvider>
)
