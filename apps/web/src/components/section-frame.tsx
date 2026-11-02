import { cn } from "cn"
import { ChevronLeftIcon } from "lucide-react"
import { type ReactNode, useId } from "react"

import { Button } from "@/components/ui/button"
import { useIsMobile } from "@/hooks/use-mobile"
import { switchSettings } from "@/lib/selection"

/**
 * A section: its title, lead and actions over its body. In the Settings dialog the header stays
 * put over the body, which scrolls, and on a phone it leads with Back to the section list. On a
 * page the section scrolls with the page.
 */
export function SectionFrame({
  title,
  lead,
  actions,
  variant = "dialog",
  children,
}: {
  title: string
  lead: string
  actions?: ReactNode
  variant?: "dialog" | "page"
  children: ReactNode
}) {
  const isMobile = useIsMobile()
  const titleId = useId()
  const leadId = useId()
  const dialog = variant === "dialog"
  const body = <div className="flex flex-col gap-4">{children}</div>
  return (
    <section
      aria-labelledby={titleId}
      aria-describedby={leadId}
      className={cn("flex min-w-0 flex-col", dialog ? "min-h-0 flex-1" : "gap-3")}
    >
      <header
        className={cn(
          "flex shrink-0 flex-col gap-1",
          // The right padding clears the dialog's Close button.
          dialog && "px-6 pt-5 pr-16 pb-3 max-md:px-4 max-md:pr-14",
        )}
      >
        <div className="flex min-h-8 flex-wrap items-center gap-2">
          {dialog && isMobile && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Back"
              onClick={() => switchSettings(undefined)}
            >
              <ChevronLeftIcon />
            </Button>
          )}
          <h2
            id={titleId}
            className={cn("font-heading font-semibold", dialog ? "text-lg" : "text-base")}
          >
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
      {dialog ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-2 pb-6 max-md:px-4">{body}</div>
      ) : (
        body
      )}
    </section>
  )
}
