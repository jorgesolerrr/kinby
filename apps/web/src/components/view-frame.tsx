import type { View } from "@kinby/contract"
import {
  type CSSProperties,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"

import { postedHeight, viewDocument } from "@/lib/view-document"

/**
 * A view's page, drawn in a frame with an opaque origin: its scripts run and it may open a tab, but
 * it reaches neither the web app nor the network. The frame is as tall as the page says it is, and
 * the app hears only its own frame's window.
 */
export function ViewFrame({
  title,
  page,
  data,
}: {
  title: string
  page: string
  data: View["data"]
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(0)
  const dark = useSyncExternalStore(onRootClassChange, rootIsDark)
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
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
      srcDoc={drawn}
      className="block h-(--page-height) w-full border-0"
      style={{ "--page-height": `${height}px` } as CSSProperties}
    />
  )
}

/** Call `listener` each time the root's classes change, as the theme store's switch does. */
function onRootClassChange(listener: () => void): () => void {
  const observer = new MutationObserver(listener)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
  return () => observer.disconnect()
}

function rootIsDark(): boolean {
  return document.documentElement.classList.contains("dark")
}
