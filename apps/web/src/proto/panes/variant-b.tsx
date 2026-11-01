// PROTOTYPE: B · Stage. A pane takes the main area, as ChatGPT's canvas and Codex do: the thread
// narrows to a fixed column on the left and the panes stand beside it as tabs. No handle; the
// thread folds away instead. On a phone the pane fills the screen and a switch at the top flips
// between the thread and each pane. Throwaway.
import { PanelLeftCloseIcon, PanelLeftOpenIcon, XIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useIsMobile } from "@/hooks/use-mobile"
import { cn } from "cn"

import { PaneBody, paneIcon, paneSubtitle, paneTitle } from "./shared"
import type { PanesProps } from "./variant-a"

export function PanesB({ thread, panes, active, setActive, close }: PanesProps) {
  const pane = panes.find((p) => p.id === active) ?? panes.at(-1)
  const isMobile = useIsMobile()
  const [threadShown, setThreadShown] = useState(true)
  const [onPhone, setOnPhone] = useState<"thread" | "pane">("pane")

  if (pane === undefined) return <>{thread}</>

  if (isMobile) {
    const shown = onPhone === "thread" ? "thread" : pane.id
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <ToggleGroup
          value={[shown]}
          onValueChange={(v: string[]) => {
            const next = v[0]
            if (next === undefined) return
            if (next === "thread") setOnPhone("thread")
            else {
              setOnPhone("pane")
              setActive(next)
            }
          }}
          variant="outline"
          size="sm"
          className="mx-3 mb-2 w-[calc(100%-1.5rem)] *:flex-1"
        >
          <ToggleGroupItem value="thread">Thread</ToggleGroupItem>
          {panes.map((p) => (
            <ToggleGroupItem key={p.id} value={p.id} className="truncate">
              {paneTitle(p)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        {onPhone === "thread" ? (
          thread
        ) : (
          <section aria-label={paneTitle(pane)} className="flex min-h-0 flex-1 flex-col border-t">
            <div className="flex items-center gap-2 px-4 pt-3">
              <span className="flex-1 text-xs text-muted-foreground">{paneSubtitle(pane)}</span>
              <Button variant="ghost" size="xs" onClick={() => close(pane.id)}>
                Close
              </Button>
            </div>
            <PaneBody pane={pane} compact />
          </section>
        )}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1">
      {threadShown && <div className="flex w-[420px] shrink-0 flex-col border-r">{thread}</div>}
      <div className="flex min-w-0 flex-1 flex-col bg-muted/40">
        <div className="flex h-11 shrink-0 items-end gap-1 border-b px-2">
          <Button
            variant="ghost"
            size="icon-sm"
            className="mb-1.5"
            aria-label={threadShown ? "Hide the thread" : "Show the thread"}
            onClick={() => setThreadShown((s) => !s)}
          >
            {threadShown ? <PanelLeftCloseIcon /> : <PanelLeftOpenIcon />}
          </Button>
          {panes.map((p) => {
            const Icon = paneIcon(p)
            const on = p.id === pane.id
            return (
              <div
                key={p.id}
                className={cn(
                  "group/tab -mb-px flex h-9 max-w-56 items-center gap-1.5 rounded-t-md border border-transparent pr-1 pl-3 text-sm",
                  on ? "border-border border-b-background bg-background" : "text-muted-foreground",
                )}
              >
                <button
                  type="button"
                  className="flex min-w-0 items-center gap-1.5"
                  onClick={() => setActive(p.id)}
                >
                  <Icon className="size-3.5 shrink-0" />
                  <span className="truncate">{paneTitle(p)}</span>
                  {p.kind === "takeover" && <span className="size-1.5 rounded-full bg-ember" />}
                </button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Close ${paneTitle(p)}`}
                  className={cn(!on && "opacity-0 group-hover/tab:opacity-100")}
                  onClick={() => close(p.id)}
                >
                  <XIcon />
                </Button>
              </div>
            )
          })}
        </div>
        <section
          aria-label={paneTitle(pane)}
          className="flex min-h-0 flex-1 flex-col bg-background"
        >
          <PaneBody pane={pane} />
        </section>
      </div>
    </div>
  )
}
