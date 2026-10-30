import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { useId } from "react"

import { ChannelsPage } from "@/components/channels-page"
import { HubSecretsPage } from "@/components/hub-secrets-page"
import { PageBoundary } from "@/components/page-boundary"
import { RemovedInstancesPage } from "@/components/removed-instances-page"
import { UsagePage } from "@/components/usage-page"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useIsMobile } from "@/hooks/use-mobile"
import { closeSettings, type SettingsSection, switchSettings } from "@/lib/selection"
import { isTheme, useTheme } from "@/lib/theme"
import { theme } from "@/theme"

const SECTIONS: { id: SettingsSection; label: string }[] = [
  { id: "general", label: "General" },
  { id: "usage", label: "Usage" },
  { id: "secrets", label: "Hub secrets" },
  { id: "channels", label: "Channels" },
  { id: "instances", label: "Instances" },
]

/**
 * Hub admin over the open page: the list of sections, and the open one's body beside it, or under
 * it on a narrow screen. `section` is the one the URL opens, and none closes the dialog.
 * `onChanged` lists the instances again.
 */
export function SettingsDialog({
  section,
  client,
  clock,
  removed,
  onChanged,
}: {
  section: SettingsSection | undefined
  client: Pick<Client, "call">
  clock: Clock
  removed: InstanceSummary[] | undefined
  onChanged: () => Promise<void>
}) {
  const isMobile = useIsMobile()
  const shown = section ?? "general"
  return (
    <Dialog open={section !== undefined} onOpenChange={(open) => !open && closeSettings()}>
      <DialogContent size="lg">
        <Tabs
          value={shown}
          onValueChange={(picked: SettingsSection) => switchSettings(picked)}
          orientation={isMobile ? "horizontal" : "vertical"}
          className="min-h-0 flex-1"
        >
          <div className="flex shrink-0 flex-col gap-3 border-b p-4 md:w-52 md:border-r md:border-b-0">
            <DialogTitle>Settings</DialogTitle>
            <TabsList
              variant="line"
              className="w-full justify-start overflow-x-auto md:items-stretch"
            >
              {SECTIONS.map(({ id, label }) => (
                <TabsTrigger key={id} value={id} className="flex-none">
                  {label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <TabsContent value={shown} className="min-h-0 min-w-0 overflow-y-auto">
            {/* Clears the dialog's Close button, which sits over the section's top right. */}
            <div className="md:pt-6">
              <PageBoundary path={shown}>
                <SectionBody
                  section={shown}
                  client={client}
                  clock={clock}
                  removed={removed}
                  onChanged={onChanged}
                />
              </PageBoundary>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

function SectionBody({
  section,
  client,
  clock,
  removed,
  onChanged,
}: {
  section: SettingsSection
  client: Pick<Client, "call">
  clock: Clock
  removed: InstanceSummary[] | undefined
  onChanged: () => Promise<void>
}) {
  switch (section) {
    case "general":
      return <General />
    case "usage":
      return <UsagePage client={client} clock={clock} />
    case "secrets":
      return <HubSecretsPage client={client} clock={clock} />
    case "channels":
      return <ChannelsPage client={client} clock={clock} />
    case "instances":
      return (
        <RemovedInstancesPage
          caller={client}
          clock={clock}
          removed={removed}
          onChanged={onChanged}
        />
      )
  }
}

/** The theme choice, which the account menu held. */
function General() {
  const current = useTheme(theme)
  const labelId = useId()
  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="font-heading text-xl font-semibold">General</h1>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span id={labelId} className="font-medium">
          Theme
        </span>
        <ToggleGroup
          aria-labelledby={labelId}
          size="sm"
          variant="outline"
          value={[current]}
          onValueChange={([picked]) => {
            if (picked !== undefined && isTheme(picked)) theme.setTheme(picked)
          }}
        >
          <ToggleGroupItem value="light">Light</ToggleGroupItem>
          <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
          <ToggleGroupItem value="system">System</ToggleGroupItem>
        </ToggleGroup>
      </div>
    </div>
  )
}
