import type { LucideIcon } from "lucide-react"
import {
  type ReactElement,
  type ReactNode,
  useContext,
  useEffect,
  useEffectEvent,
  useState,
} from "react"

import { Button } from "@/components/ui/button"
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from "@/components/ui/drawer"
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable"
import { SidebarContext } from "@/components/ui/sidebar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useIsMobile } from "@/hooks/use-mobile"
import { ExternalLinkIcon, Maximize2Icon, Minimize2Icon, XIcon } from "lucide-react"

/** What a pane open beside a thread shows: its heading, then what it holds. */
export interface OpenPane {
  icon: LucideIcon
  title: string
  /** The line under the title, saying where what the pane holds stands. */
  status: string
  /** Open what the pane holds on its own page, when it has one. */
  openAsPage?: () => void
  body: ReactNode
}

/**
 * A thread and, when one is open, the pane beside it, split by a handle that resizes them. Each
 * open starts from the same split. The pane's header can hide the thread and close the pane. On a
 * phone the pane rises as a drawer over the thread instead, and swiping it down closes it. The
 * thread stays mounted throughout, across the phone's breakpoint too, so its scroll and the
 * composer's draft survive the pane.
 */
export function ThreadSplit({
  thread,
  pane,
  onClose,
}: {
  thread: ReactNode
  pane: OpenPane | undefined
  onClose: () => void
}) {
  const open = pane !== undefined
  const isMobile = useIsMobile()
  const [hidden, setHidden] = useState(false)
  // Hiding the thread belongs to the pane it was hidden for, so the next open shows the thread.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    setHidden(false)
  }
  useFoldSidebar(open && !isMobile)

  return (
    <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
      <ResizablePanel
        id="thread"
        defaultSize="44%"
        minSize="360px"
        hidden={hidden && !isMobile}
        className="flex flex-col"
      >
        {thread}
      </ResizablePanel>
      {isMobile && <PaneDrawer pane={pane} onClose={onClose} />}
      {!isMobile && pane !== undefined && (
        <>
          <ResizableHandle withHandle variant="ghost" hidden={hidden} className="mx-1" />
          <ResizablePanel id="pane" defaultSize="56%" minSize="380px">
            <div className={hidden ? "flex h-full flex-col p-2" : "flex h-full flex-col p-2 pl-0"}>
              <section
                aria-label={pane.title}
                className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border bg-card"
              >
                <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
                  <pane.icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm font-medium">{pane.title}</span>
                    <span className="truncate text-xs text-muted-foreground">{pane.status}</span>
                  </div>
                  {pane.openAsPage !== undefined && (
                    <Hint label="Open as a page">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Open as a page"
                        onClick={pane.openAsPage}
                      >
                        <ExternalLinkIcon />
                      </Button>
                    </Hint>
                  )}
                  <Hint label={hidden ? "Show the thread" : "Hide the thread"}>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={hidden ? "Show the thread" : "Hide the thread"}
                      onClick={() => setHidden(!hidden)}
                    >
                      {hidden ? <Minimize2Icon /> : <Maximize2Icon />}
                    </Button>
                  </Hint>
                  <Hint label="Close">
                    <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
                      <XIcon />
                    </Button>
                  </Hint>
                </header>
                {pane.body}
              </section>
            </div>
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}

/** The open pane as a drawer over the thread, for a phone. */
function PaneDrawer({ pane, onClose }: { pane: OpenPane | undefined; onClose: () => void }) {
  return (
    <Drawer open={pane !== undefined} onOpenChange={(opened) => !opened && onClose()}>
      <DrawerContent size="tall">
        {pane !== undefined && (
          <>
            <header className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
              <pane.icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
              <div className="flex min-w-0 flex-1 flex-col">
                <DrawerTitle>
                  <span className="block truncate">{pane.title}</span>
                </DrawerTitle>
                <DrawerDescription>
                  <span className="block truncate">{pane.status}</span>
                </DrawerDescription>
              </div>
              <DrawerClose render={<Button variant="ghost" size="icon-sm" aria-label="Close" />}>
                <XIcon />
              </DrawerClose>
            </header>
            {pane.body}
          </>
        )}
      </DrawerContent>
    </Drawer>
  )
}

function Hint({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

/**
 * Fold the sidebar while `folded`, and give it back as it was. Rendered outside the sidebar's
 * provider, as on its own in a test, there is no sidebar to fold.
 */
function useFoldSidebar(folded: boolean) {
  const sidebar = useContext(SidebarContext)
  const isOpen = useEffectEvent(() => sidebar?.open)
  const setOpen = useEffectEvent((open: boolean) => sidebar?.setOpen(open))
  useEffect(() => {
    const before = isOpen()
    if (!folded || before === undefined) return
    setOpen(false)
    return () => setOpen(before)
  }, [folded])
}
