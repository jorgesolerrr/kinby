// PROTOTYPE, throwaway. Floating bar that cycles `?variant=` on a prototype route.
import { useEffect } from "react"

import { Button } from "@/components/ui/button"
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"

export function PrototypeSwitcher({
  variants,
  current,
  onChange,
}: {
  variants: { key: string; name: string }[]
  current: string
  onChange: (key: string) => void
}) {
  const index = Math.max(
    0,
    variants.findIndex((v) => v.key === current),
  )
  const go = (step: number) =>
    onChange(variants[(index + step + variants.length) % variants.length].key)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.closest("input, textarea, [contenteditable]")) return
      if (event.key === "ArrowLeft") go(-1)
      if (event.key === "ArrowRight") go(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  if (!import.meta.env.DEV) return null
  const variant = variants[index]
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full bg-foreground p-1 text-background shadow-xl">
      <Button size="icon-sm" variant="ghost" onClick={() => go(-1)} aria-label="Previous variant">
        <ChevronLeftIcon />
      </Button>
      <span className="px-2 text-sm font-medium">
        {variant.key} ({variant.name})
      </span>
      <Button size="icon-sm" variant="ghost" onClick={() => go(1)} aria-label="Next variant">
        <ChevronRightIcon />
      </Button>
    </div>
  )
}
