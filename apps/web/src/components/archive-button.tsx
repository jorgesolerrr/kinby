import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { ArchiveIcon, ArchiveRestoreIcon } from "lucide-react"

/**
 * Archive puts a thread away, out of the sidebar; unarchive clears that, and the sidebar rule
 * decides whether it shows again. It shows a spinner and takes no clicks while `onToggle` is out.
 */
export function ArchiveButton({
  archived,
  onToggle,
}: {
  archived: boolean
  onToggle: () => Promise<unknown>
}) {
  const [changing, setChanging] = useState(false)
  const toggle = async () => {
    if (changing) return
    setChanging(true)
    await onToggle()
    setChanging(false)
  }
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={archived ? "Unarchive" : "Archive"}
      onClick={() => void toggle()}
    >
      {changing ? <Spinner /> : archived ? <ArchiveRestoreIcon /> : <ArchiveIcon />}
    </Button>
  )
}
