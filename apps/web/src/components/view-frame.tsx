import type { View } from "@kinby/contract"
import { cn } from "cn"
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react"

import { postedHeight, viewDocument } from "@/lib/view-document"

/**
 * A view's page, drawn in a frame with an opaque origin: its scripts run and it may open a tab, but
 * it reaches neither the web app nor the network. The frame is as tall as the page says it is, and
 * the app hears only its own frame's window. A frame drawn without `scripts` runs none, the bridge's
 * included, so it is a fixed height and scrolls inside.
 */
export function ViewFrame({
  title,
  page,
  data,
  scripts = true,
}: {
  title: string
  page: string
  data: View["data"]
  scripts?: boolean
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(0)
  const dark = document.documentElement.classList.contains("dark")
  const drawn = useMemo(
    () => viewDocument(page, data, { dark, origin: window.location.origin }),
    [page, data, dark],
  )
  useEffect(() => {
    const heard = (event: MessageEvent) => {
      if (frame.current === null || event.source !== frame.current.contentWindow) return
      const posted = postedHeight(event.data)
      if (posted !== undefined) setHeight(posted)
    }
    window.addEventListener("message", heard)
    return () => window.removeEventListener("message", heard)
  }, [])
  return (
    <iframe
      ref={frame}
      title={title}
      sandbox={scripts ? "allow-scripts allow-popups allow-popups-to-escape-sandbox" : ""}
      srcDoc={drawn}
      className={cn("block w-full border-0", scripts ? "h-(--page-height)" : "h-96")}
      style={{ "--page-height": `${height}px` } as CSSProperties}
    />
  )
}
