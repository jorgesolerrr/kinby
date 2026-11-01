import type { Client, Clock, InstanceSummary } from "@kinby/contract"
import { ChevronRightIcon } from "lucide-react"
import { useId } from "react"

import { ChannelsSection } from "@/components/channels-section"
import { HubSecretsSection } from "@/components/hub-secrets-section"
import { InstancesSection } from "@/components/instances-section"
import { PageBoundary } from "@/components/page-boundary"
import { SectionFrame } from "@/components/section-frame"
import { UsageSection } from "@/components/usage-section"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
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
 * Hub admin over the open page: the list of sections, and the open one beside it. With no section
 * open, a phone shows the list alone and the desktop shows General. `onChanged` lists the instances
 * again.
 */
export function SettingsDialog({
  open,
  section,
  client,
  clock,
  removed,
  onChanged,
}: {
  open: boolean
  section: SettingsSection | undefined
  client: Pick<Client, "call">
  clock: Clock
  removed: InstanceSummary[] | undefined
  onChanged: () => Promise<void>
}) {
  const isMobile = useIsMobile()
  const shown = section ?? (isMobile ? undefined : "general")
  return (
    <Dialog open={open} onOpenChange={(opened) => !opened && closeSettings()}>
      <DialogContent size="lg">
        {isMobile && shown !== undefined ? (
          <DialogTitle className="sr-only">Settings</DialogTitle>
        ) : (
          <nav
            aria-label="Sections"
            className="flex shrink-0 flex-col gap-1 p-3 max-md:flex-1 md:w-52 md:border-r"
          >
            <DialogTitle className="mx-2 mt-2 mb-3">Settings</DialogTitle>
            {SECTIONS.map(({ id, label }) => (
              <Button
                key={id}
                variant="nav"
                size={isMobile ? "lg" : "default"}
                aria-current={id === shown ? "page" : undefined}
                onClick={() => switchSettings(id)}
              >
                {isMobile ? (
                  <>
                    <span className="flex-1 text-start">{label}</span>
                    <ChevronRightIcon className="text-muted-foreground" />
                  </>
                ) : (
                  label
                )}
              </Button>
            ))}
          </nav>
        )}
        {shown !== undefined && (
          <PageBoundary path={shown}>
            <SectionBody
              section={shown}
              client={client}
              clock={clock}
              removed={removed}
              onChanged={onChanged}
            />
          </PageBoundary>
        )}
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
      return <UsageSection client={client} clock={clock} />
    case "secrets":
      return <HubSecretsSection client={client} clock={clock} />
    case "channels":
      return <ChannelsSection client={client} clock={clock} />
    case "instances":
      return (
        <InstancesSection caller={client} clock={clock} removed={removed} onChanged={onChanged} />
      )
  }
}

/** The theme choice, which the account menu held. */
function General() {
  const current = useTheme(theme)
  const labelId = useId()
  return (
    <SectionFrame title="General" lead="How kinby looks on this device.">
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
    </SectionFrame>
  )
}
