import { Toggle } from "@kinby/web"
import { BellIcon, BoldIcon, ItalicIcon, PinIcon, StarIcon } from "lucide-react"

export const Variants = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Toggle>
      <PinIcon />
      Pin thread
    </Toggle>
    <Toggle variant="outline">
      <BellIcon />
      Notify me
    </Toggle>
  </div>
)

export const Pressed = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Toggle defaultPressed>
      <StarIcon />
      Starred
    </Toggle>
    <Toggle variant="outline" defaultPressed>
      <BellIcon />
      Notifying
    </Toggle>
    <Toggle variant="outline">
      <BellIcon />
      Muted
    </Toggle>
  </div>
)

export const Sizes = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Toggle variant="outline" size="sm">
      Small
    </Toggle>
    <Toggle variant="outline">Default</Toggle>
    <Toggle variant="outline" size="lg">
      Large
    </Toggle>
  </div>
)

export const IconOnly = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Toggle aria-label="Bold" defaultPressed>
      <BoldIcon />
    </Toggle>
    <Toggle aria-label="Italic">
      <ItalicIcon />
    </Toggle>
    <Toggle variant="outline" aria-label="Pin" disabled>
      <PinIcon />
    </Toggle>
  </div>
)
