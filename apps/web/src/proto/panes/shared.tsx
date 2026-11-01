// PROTOTYPE: what a pane holds, drawn the same in both variants. Only the frame around it differs.
import {
  FlagIcon,
  GlobeIcon,
  LayoutDashboardIcon,
  LockIcon,
  RotateCwIcon,
  type LucideIcon,
} from "lucide-react"
import type * as React from "react"

import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { ViewRenderer } from "@/components/view-renderer"
import { cn } from "cn"

import { TRIPS } from "./mock"
import type { Pane } from "./slot"

export const paneIcon = (pane: Pane): LucideIcon =>
  pane.kind === "takeover" ? GlobeIcon : LayoutDashboardIcon

export const paneTitle = (pane: Pane) => (pane.kind === "takeover" ? pane.site : pane.title)

export const paneSubtitle = (pane: Pane) =>
  pane.kind === "takeover" ? "Kin is waiting on you" : "Changed 2 minutes ago"

/** The pane's body: the live browser with its answers, or the view. */
export function PaneBody({ pane, compact }: { pane: Pane; compact?: boolean }) {
  return pane.kind === "takeover" ? (
    <TakeoverBody pane={pane} compact={compact} />
  ) : (
    <div className="min-h-0 flex-1 overflow-y-auto p-4 text-sm">
      <ViewRenderer view={TRIPS} />
    </div>
  )
}

function TakeoverBody({
  pane,
  compact,
}: {
  pane: Extract<Pane, { kind: "takeover" }>
  compact?: boolean
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="px-4 pt-3 pb-2 text-sm text-muted-foreground">{pane.reason}</p>
      <div className="min-h-0 flex-1 px-4">
        <LiveBrowserMock site={pane.site} />
      </div>
      <div
        className={cn(
          "flex gap-3 border-t p-4",
          compact ? "flex-col" : "flex-wrap items-end justify-between",
        )}
      >
        <Field className={compact ? "" : "max-w-64"}>
          <FieldLabel htmlFor="profile">Save as a profile</FieldLabel>
          <Input id="profile" placeholder="TheFork" />
          {!compact && <FieldDescription>Kin opens the site signed in next time.</FieldDescription>}
        </Field>
        <div className="flex items-center gap-2">
          <Button variant="ghost">Stop the turn</Button>
          <Button>
            <FlagIcon data-icon="inline-start" />
            Finish
          </Button>
        </div>
      </div>
    </div>
  )
}

/** A stand-in for the streamed page: a browser frame around a sign-in form. */
function LiveBrowserMock({ site }: { site: string }) {
  return (
    <div className="flex h-full min-h-72 flex-col overflow-hidden rounded-lg border bg-background">
      <div className="flex items-center gap-2 border-b bg-muted px-3 py-1.5 text-xs text-muted-foreground">
        <RotateCwIcon className="size-3.5" />
        <span className="flex flex-1 items-center gap-1.5 rounded-md bg-background px-2 py-1">
          <LockIcon className="size-3" />
          {site}/login
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-kin" />
          Live
        </span>
      </div>
      <div className="flex flex-1 items-center justify-center bg-[#f4f6f3] p-6 text-[#1b1b1b]">
        <div className="flex w-full max-w-72 flex-col gap-3 rounded-md bg-white p-5 shadow-sm">
          <b className="text-lg text-[#00665c]">TheFork</b>
          <span className="text-sm">Sign in to complete your booking</span>
          <Fake>Email</Fake>
          <Fake>Password</Fake>
          <span className="rounded-md bg-[#00665c] py-2 text-center text-sm text-white">
            Continue
          </span>
          <span className="text-center text-xs text-[#666]">or continue with Google</span>
        </div>
      </div>
    </div>
  )
}

const Fake = ({ children }: { children: React.ReactNode }) => (
  <span className="rounded-md border border-[#ccc] px-2 py-1.5 text-sm text-[#888]">
    {children}
  </span>
)
