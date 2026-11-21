import type { View } from "@kinby/contract"
import { CheckIcon, CircleAlertIcon, CopyIcon } from "lucide-react"
import {
  type CSSProperties,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { postedError, postedHeight, viewDocument } from "@/lib/view-document"

/** As many of the errors a page raises as the instance keeps. */
const ERRORS_KEPT = 10

/**
 * A view's page, drawn in a frame with an opaque origin: its scripts run and it may open a tab, but
 * it reaches neither the web app nor the network. A new page reloads the frame, and new data or a
 * new theme is posted into the page it has loaded. The frame is as tall as the page says it is,
 * and the app hears only its own frame's window. The errors the page raises show above it, each
 * once, until a new page or new data, and `onErrors` hears the list each time it grows.
 */
export function ViewFrame({
  title,
  page,
  data,
  onErrors,
}: {
  title: string
  page: string
  data: View["data"]
  onErrors?: (errors: string[]) => void
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(0)
  const [errors, setErrors] = useState<string[]>([])
  const dark = useSyncExternalStore(onRootClassChange, rootIsDark)
  const [drawn, setDrawn] = useState(() => draw(page, data, dark))
  if (drawn.page !== page) {
    setDrawn(draw(page, data, dark))
    setErrors([])
  }
  // What the page the frame has loaded shows, undefined until it loads.
  const shown = useRef<Drawn>(undefined)
  const follow = () => {
    const target = frame.current?.contentWindow
    const held = shown.current
    if (target == null || held?.document !== drawn.document) return
    if (held.data !== data) {
      target.postMessage({ kinby: "data", data }, "*")
      setErrors([])
    }
    if (held.dark !== dark) target.postMessage({ kinby: "theme", dark }, "*")
    shown.current = { ...drawn, data, dark }
  }
  useEffect(follow)
  useEffect(() => {
    const heard = (event: MessageEvent) => {
      if (frame.current === null || event.source !== frame.current.contentWindow) return
      const posted = postedHeight(event.data)
      if (posted !== undefined) setHeight(posted)
      const error = postedError(event.data)
      if (error === undefined) return
      setErrors((shown) =>
        shown.includes(error) || shown.length >= ERRORS_KEPT ? shown : [...shown, error],
      )
    }
    window.addEventListener("message", heard)
    return () => window.removeEventListener("message", heard)
  }, [])
  const report = useEffectEvent((raised: string[]) => onErrors?.(raised))
  useEffect(() => {
    if (errors.length > 0) report(errors)
  }, [errors])
  return (
    <>
      {errors.length > 0 && <PageErrors errors={errors} />}
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
    </>
  )
}

function PageErrors({ errors }: { errors: string[] }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(errors.join("\n"))
    setCopied(true)
    setTimeout(() => setCopied(false), 2_000)
  }
  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertTitle>
        The page raised {errors.length === 1 ? "an error" : `${errors.length} errors`}
      </AlertTitle>
      <AlertDescription>
        <ul>
          {errors.map((error) => (
            <li key={error} className="font-mono break-all">
              {error}
            </li>
          ))}
        </ul>
      </AlertDescription>
      <AlertAction>
        <Button size="sm" variant="outline" onClick={() => void copy()}>
          {copied ? <CheckIcon /> : <CopyIcon />}
          Copy
        </Button>
      </AlertAction>
    </Alert>
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
