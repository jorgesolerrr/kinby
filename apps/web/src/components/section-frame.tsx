import { ChevronLeftIcon } from "lucide-react"
import { type ReactNode, useId } from "react"

import { Button } from "@/components/ui/button"
import { useIsMobile } from "@/hooks/use-mobile"
import { switchSettings } from "@/lib/selection"

/**
 * A Settings section: its title, lead and actions in a header that stays put, over its body, which
 * scrolls. On a phone the header leads with Back to the section list.
 */
export function SectionFrame({
  title,
  lead,
  actions,
  children,
}: {
  title: string
  lead: string
  actions?: ReactNode
  children: ReactNode
}) {
  const isMobile = useIsMobile()
  const titleId = useId()
  const leadId = useId()
  return (
    <section
      aria-labelledby={titleId}
      aria-describedby={leadId}
      className="flex min-h-0 min-w-0 flex-1 flex-col"
    >
      {/* The right padding clears the dialog's Close button. */}
      <header className="flex shrink-0 flex-col gap-1 px-6 pt-5 pr-16 pb-3 max-md:px-4 max-md:pr-14">
        <div className="flex min-h-8 flex-wrap items-center gap-2">
          {isMobile && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Back"
              onClick={() => switchSettings(undefined)}
            >
              <ChevronLeftIcon />
            </Button>
          )}
          <h2 id={titleId} className="font-heading text-lg font-semibold">
            {title}
          </h2>
          {actions !== undefined && (
            <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>
          )}
        </div>
        <p id={leadId} className="text-sm text-muted-foreground">
          {lead}
        </p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-2 pb-6 max-md:px-4">
        <div className="flex flex-col gap-4">{children}</div>
      </div>
    </section>
  )
}
