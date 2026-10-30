import type { ReactNode } from "react"

import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import { Spinner } from "@/components/ui/spinner"
import { nodePath, openNode } from "@/lib/selection"
import type { Recap, TurnBlock } from "@/lib/timeline"
import {
  BrainIcon,
  CheckIcon,
  CircleAlertIcon,
  CircleStopIcon,
  MinusIcon,
  TriangleAlertIcon,
} from "lucide-react"

export function EndMarker({ turn }: { turn: TurnBlock }) {
  const { icon, outcome, variant } = turnOutcome(turn)
  return (
    <Marker variant={variant}>
      <MarkerIcon>{icon}</MarkerIcon>
      <MarkerContent>
        <span className="font-mono text-xs">{outcome}</span>
      </MarkerContent>
    </Marker>
  )
}

/** How the turn ended, or that it is still working. */
function turnOutcome({ end, steps }: TurnBlock): {
  icon: ReactNode
  outcome: ReactNode
  variant?: "destructive"
} {
  switch (end?.kind) {
    case undefined:
      return { icon: <Spinner />, outcome: <span className="shimmer">Working</span> }
    case "done": {
      const calls = steps.filter((step) => step.kind === "tool").length
      const tokens = end.tokens.toLocaleString("en")
      return {
        icon: <CheckIcon />,
        outcome: `Done · ${calls} ${calls === 1 ? "step" : "steps"} · ${tokens} tokens`,
      }
    }
    case "failed":
      return {
        icon: <CircleAlertIcon />,
        outcome: `Failed: ${end.message} (${end.code}). Send a message to try again.`,
        variant: "destructive",
      }
    case "stopped":
      return { icon: <CircleStopIcon />, outcome: "Stopped" }
  }
}

/** What the turn's recap left in memory, linking to the episode it kept. */
export function RecapMarker({ recap, instanceId }: { recap: Recap; instanceId: string }) {
  switch (recap.kind) {
    case "episode":
      return (
        <Marker>
          <MarkerIcon>
            <BrainIcon />
          </MarkerIcon>
          <MarkerContent>
            <a
              className="font-mono text-xs"
              href={nodePath(instanceId, recap.node)}
              onClick={(event) => {
                event.preventDefault()
                openNode(instanceId, recap.node)
              }}
            >
              Recapped
            </a>
          </MarkerContent>
        </Marker>
      )
    case "none":
      return (
        <Marker>
          <MarkerIcon>
            <MinusIcon />
          </MarkerIcon>
          <MarkerContent>
            <span className="font-mono text-xs">No episode</span>
          </MarkerContent>
        </Marker>
      )
    case "failed":
      return (
        // The reason shows inline, so touch and keyboard readers get it too.
        <Marker className="items-start">
          <MarkerIcon>
            <TriangleAlertIcon />
          </MarkerIcon>
          <MarkerContent className="flex flex-col">
            <span className="font-mono text-xs">Recap failed</span>
            <span className="font-mono text-xs">{recap.message}</span>
          </MarkerContent>
        </Marker>
      )
  }
}
