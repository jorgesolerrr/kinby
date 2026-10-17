import { ToggleGroup, ToggleGroupItem } from "@kinby/web"
import {
  AlignCenterIcon,
  AlignLeftIcon,
  AlignRightIcon,
  LayoutGridIcon,
  ListIcon,
} from "lucide-react"

export const Range = () => (
  <div className="flex flex-wrap items-center gap-2">
    <ToggleGroup aria-label="Range" size="sm" variant="outline" defaultValue={["30"]}>
      {[7, 30, 90].map((days) => (
        <ToggleGroupItem key={days} value={String(days)}>
          {days} days
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
    <span className="text-xs text-muted-foreground">Daily buckets, UTC</span>
  </div>
)

export const Revision = () => (
  <ToggleGroup aria-label="Revision" variant="outline" defaultValue={["hub"]}>
    <ToggleGroupItem value="hub">The hub's revision</ToggleGroupItem>
    <ToggleGroupItem value="ref">Another ref</ToggleGroupItem>
  </ToggleGroup>
)

export const Joined = () => (
  <div className="flex flex-wrap items-center gap-4">
    <ToggleGroup aria-label="View" variant="outline" spacing={0} defaultValue={["list"]}>
      <ToggleGroupItem value="list" aria-label="List">
        <ListIcon />
      </ToggleGroupItem>
      <ToggleGroupItem value="grid" aria-label="Grid">
        <LayoutGridIcon />
      </ToggleGroupItem>
    </ToggleGroup>
    <ToggleGroup aria-label="Align" spacing={0} defaultValue={["left"]}>
      <ToggleGroupItem value="left" aria-label="Left">
        <AlignLeftIcon />
      </ToggleGroupItem>
      <ToggleGroupItem value="center" aria-label="Center">
        <AlignCenterIcon />
      </ToggleGroupItem>
      <ToggleGroupItem value="right" aria-label="Right">
        <AlignRightIcon />
      </ToggleGroupItem>
    </ToggleGroup>
  </div>
)

export const Vertical = () => (
  <ToggleGroup
    aria-label="Thread status"
    orientation="vertical"
    variant="outline"
    spacing={0}
    defaultValue={["needs-you"]}
  >
    <ToggleGroupItem value="all">All threads</ToggleGroupItem>
    <ToggleGroupItem value="needs-you">Needs you</ToggleGroupItem>
    <ToggleGroupItem value="running">Running</ToggleGroupItem>
  </ToggleGroup>
)
