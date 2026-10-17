import { Button } from "@kinby/web"
import { ArrowRightIcon, PlusIcon, Trash2Icon, SettingsIcon } from "lucide-react"

export const Variants = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button>Start a thread</Button>
    <Button variant="secondary">Open memory</Button>
    <Button variant="outline">Edit routine</Button>
    <Button variant="ghost">Cancel</Button>
    <Button variant="destructive">Remove instance</Button>
    <Button variant="link">View usage</Button>
  </div>
)

export const Sizes = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button size="xs">Extra small</Button>
    <Button size="sm">Small</Button>
    <Button>Default</Button>
    <Button size="lg">Large</Button>
  </div>
)

export const WithIcons = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button>
      <PlusIcon data-icon="inline-start" />
      New instance
    </Button>
    <Button variant="outline">
      Continue
      <ArrowRightIcon data-icon="inline-end" />
    </Button>
    <Button variant="destructive">
      <Trash2Icon data-icon="inline-start" />
      Delete skill
    </Button>
  </div>
)

export const IconOnly = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button size="icon-xs" variant="ghost" aria-label="Settings">
      <SettingsIcon />
    </Button>
    <Button size="icon-sm" variant="outline" aria-label="Settings">
      <SettingsIcon />
    </Button>
    <Button size="icon" variant="secondary" aria-label="Add">
      <PlusIcon />
    </Button>
    <Button size="icon-lg" aria-label="Add">
      <PlusIcon />
    </Button>
  </div>
)

export const Disabled = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button disabled>Saving…</Button>
    <Button variant="outline" disabled>
      Unavailable
    </Button>
  </div>
)
