// PROTOTYPE: A · Split. One pane at a time beside the thread, as Claude opens an artifact. The
// handle resizes the split, the sidebar folds away while a pane is open, and opening another pane
// replaces the one shown. On a phone the pane rises as a drawer over the thread. Throwaway.
import { ExternalLinkIcon, Maximize2Icon, Minimize2Icon, XIcon } from "lucide-react"
import type * as React from "react"
import { useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer"
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable"
import { useSidebar } from "@/components/ui/sidebar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useIsMobile } from "@/hooks/use-mobile"

import { PaneBody, paneIcon, paneSubtitle, paneTitle } from "./shared"
import type { Pane } from "./slot"

export interface PanesProps {
  thread: React.ReactNode
  panes: Pane[]
  active: string | undefined
  setActive: (id: string) => void
  close: (id: string) => void
}

export function PanesA({ thread, panes, active, close }: PanesProps) {
  const pane = panes.find((p) => p.id === active)
  const isMobile = useIsMobile()
  const [full, setFull] = useState(false)
  useFoldSidebar(pane !== undefined && !isMobile)
  useEffect(() => setFull(false), [pane?.id])

  if (isMobile) {
    return (
      <>
        {thread}
        <Drawer open={pane !== undefined} onOpenChange={(open) => !open && pane && close(pane.id)}>
          <DrawerContent className="h-[92svh] max-h-[92svh]">
            {pane && (
              <>
                <DrawerHeader className="flex-row items-center gap-2 text-left">
                  <PaneHeading pane={pane} />
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Close"
                    onClick={() => close(pane.id)}
                  >
                    <XIcon />
                  </Button>
                </DrawerHeader>
                <PaneBody pane={pane} compact />
              </>
            )}
          </DrawerContent>
        </Drawer>
      </>
    )
  }

  if (pane === undefined) return <>{thread}</>
  const frame = (
    <div className="flex h-full flex-col p-2 pl-0 data-full:pl-2" data-full={full || undefined}>
      <section
        aria-label={paneTitle(pane)}
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border bg-card"
      >
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
          <PaneHeading pane={pane} />
          {pane.kind === "view" && (
            <Hint label="Open as a page">
              <Button variant="ghost" size="icon-sm" aria-label="Open as a page">
                <ExternalLinkIcon />
              </Button>
            </Hint>
          )}
          <Hint label={full ? "Show the thread" : "Hide the thread"}>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={full ? "Show the thread" : "Hide the thread"}
              onClick={() => setFull((f) => !f)}
            >
              {full ? <Minimize2Icon /> : <Maximize2Icon />}
            </Button>
          </Hint>
          <Hint label="Close">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close"
              onClick={() => close(pane.id)}
            >
              <XIcon />
            </Button>
          </Hint>
        </header>
        <PaneBody pane={pane} />
      </section>
    </div>
  )
  if (full) return frame
  return (
    <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
      <ResizablePanel defaultSize="44%" minSize="360px" className="flex flex-col">
        {thread}
      </ResizablePanel>
      <ResizableHandle className="mx-1 bg-transparent hover:bg-border" withHandle />
      <ResizablePanel defaultSize="56%" minSize="380px">
        {frame}
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

function PaneHeading({ pane }: { pane: Pane }) {
  const Icon = paneIcon(pane)
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-col">
        <DrawerTitleOr className="truncate text-sm font-medium">{paneTitle(pane)}</DrawerTitleOr>
        <span className="truncate text-xs text-muted-foreground">{paneSubtitle(pane)}</span>
      </div>
    </div>
  )
}

/** Inside the drawer the title must be the drawer's own; on the desktop it is plain text. */
function DrawerTitleOr({ className, children }: { className: string; children: React.ReactNode }) {
  const isMobile = useIsMobile()
  if (!isMobile) return <span className={className}>{children}</span>
  return (
    <>
      <DrawerTitle className={className}>{children}</DrawerTitle>
      <DrawerDescription className="sr-only">A side pane</DrawerDescription>
    </>
  )
}

function Hint({ label, children }: { label: string; children: React.ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

/** Folds the sidebar while a pane is open, and gives it back as it was when the pane closes. */
function useFoldSidebar(folded: boolean) {
  const { open, setOpen } = useSidebar()
  const before = useRef<boolean | undefined>(undefined)
  useEffect(() => {
    if (folded && before.current === undefined) {
      before.current = open
      setOpen(false)
    }
    if (!folded && before.current !== undefined) {
      setOpen(before.current)
      before.current = undefined
    }
  }, [folded, open, setOpen])
}
