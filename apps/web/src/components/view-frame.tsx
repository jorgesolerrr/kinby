import type { View } from "@kinby/contract"
import { type CSSProperties, useEffect, useRef, useState, useSyncExternalStore } from "react"

import { asksRedraw, postedHeight, viewDocument } from "@/lib/view-document"

/**
 * A view's page, drawn in a frame with an opaque origin: its scripts run and it may open a tab, but
 * it reaches neither the web app nor the network. A new page reloads the frame, and new data or a
 * new theme is posted into the page it has loaded, which reloads only if it hears no change. The frame is as tall as the page says it is,
 * and the app hears only its own frame's window.
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
  const [drawn, setDrawn] = useState(() => draw(page, data, dark))
  if (drawn.page !== page) setDrawn(draw(page, data, dark))
  // What the page the frame has loaded shows, undefined until it loads.
  const shown = useRef<Drawn>(undefined)
  const follow = () => {
    const target = frame.current?.contentWindow
    const held = shown.current
    if (target == null || held?.document !== drawn.document) return
    if (held.data !== data) target.postMessage({ kinby: "data", data }, "*")
    if (held.dark !== dark) target.postMessage({ kinby: "theme", dark }, "*")
    shown.current = { ...drawn, data, dark }
  }
  useEffect(follow)
  useEffect(() => {
    const heard = (event: MessageEvent) => {
      if (frame.current === null || event.source !== frame.current.contentWindow) return
      if (asksRedraw(event.data)) setDrawn(draw(page, data, dark))
      const posted = postedHeight(event.data)
      if (posted !== undefined) setHeight(posted)
    }
    window.addEventListener("message", heard)
    return () => window.removeEventListener("message", heard)
  }, [page, data, dark])
  return (
    <iframe
      ref={frame}
      title={title}
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
      srcDoc={drawn.document}
      onLoad={() => {
        shown.current = drawn
        follow()
      }}
      className="block h-(--page-height) w-full border-0"
      style={{ "--page-height": `${height}px` } as CSSProperties}
    />
  )
}

/** A page's document, and the data and theme it was drawn with. */
type Drawn = { page: string; document: string; data: View["data"]; dark: boolean }

function draw(page: string, data: View["data"], dark: boolean): Drawn {
  return {
    page,
    document: viewDocument(page, data, { dark, origin: window.location.origin }),
    data,
    dark,
  }
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
