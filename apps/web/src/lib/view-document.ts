import type { View } from "@kinby/contract"

import tokens from "@/tokens.css?raw"

/** Where the hub serves what a view's page may load, such as its base stylesheet. */
const SANDBOX_PATH = "/sandbox/"

/**
 * Runs before the page: it hands the page its data and each change the app posts to it, follows
 * the app's theme, posts the page's height and each uncaught error and policy violation out, and
 * opens a link to an http, https or mailto address in a new tab. A page that hears no change, as
 * one that reads kinby.data once, asks the app to draw it again. It keeps its names to itself, so
 * a page may use the same ones.
 */
const BRIDGE = `(() => {
  const kinby = window.kinby
  const listeners = []
  kinby.onData = (callback) => {
    listeners.push(callback)
  }
  window.addEventListener("message", (event) => {
    const message = event.data
    if (event.source !== parent || typeof message !== "object" || message === null) return
    if (message.kinby === "data") {
      kinby.data = message.data
      if (listeners.length === 0) parent.postMessage({ kinby: "redraw" }, "*")
      for (const callback of listeners) callback(message.data)
    } else if (message.kinby === "theme") {
      document.documentElement.classList.toggle("dark", message.dark === true)
    }
  })
  const links = ["http:", "https:", "mailto:"]
  const post = () => parent.postMessage(
    { kinby: "height", height: Math.ceil(document.documentElement.getBoundingClientRect().height) },
    "*",
  )
  new ResizeObserver(post).observe(document.documentElement)
  const report = (message) => parent.postMessage({ kinby: "error", message }, "*")
  window.addEventListener("error", (event) => report(event.message))
  window.addEventListener("unhandledrejection", (event) => {
    report(\`Uncaught (in promise) \${event.reason}\`)
  })
  document.addEventListener("securitypolicyviolation", (event) => {
    report(\`The page's policy blocked \${event.blockedURI} (\${event.effectiveDirective})\`)
  })
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest("a[href]") : null
    if (link === null || !URL.canParse(link.getAttribute("href"))) return
    const url = new URL(link.getAttribute("href"))
    if (!links.includes(url.protocol)) return
    event.preventDefault()
    window.open(url.href, "_blank", "noopener")
  })
})()`

/**
 * The document a view's frame draws (ADR 0131): a content security policy first, then the theme's
 * tokens for light and dark, the hub's base stylesheet and the bridge, then the page. The policy
 * reaches nothing but the hub's sandbox path. It runs the scripts that path serves, and the inline
 * scripts carrying this load's nonce: the bridge and each one the page was written with. A handler
 * written as an attribute, or script added later, never runs.
 */
export function viewDocument(
  page: string,
  data: View["data"],
  { dark, origin }: { dark: boolean; origin: string },
): string {
  const nonce = crypto.randomUUID()
  const sandbox = `${origin}${SANDBOX_PATH}`
  const drawn = new DOMParser().parseFromString(page, "text/html")
  // A nonce admits a script from any address, so a script with a src gets none and only the
  // policy's sandbox path admits it.
  for (const script of drawn.querySelectorAll("script:not([src])")) {
    script.setAttribute("nonce", nonce)
  }

  const policy = drawn.createElement("meta")
  policy.httpEquiv = "Content-Security-Policy"
  policy.content = [
    "default-src 'none'",
    `script-src 'nonce-${nonce}' ${sandbox}`,
    `style-src 'unsafe-inline' ${sandbox}`,
    "img-src data:",
    "form-action 'none'",
    "base-uri 'none'",
  ].join("; ")
  const theme = drawn.createElement("style")
  theme.textContent = tokens
  const base = drawn.createElement("link")
  base.rel = "stylesheet"
  base.href = `${sandbox}base.css`
  const bridge = drawn.createElement("script")
  bridge.setAttribute("nonce", nonce)
  // A "<" in the data would let a value close the script and open one of its own.
  const json = JSON.stringify(data).replaceAll("<", "\\u003c")
  bridge.textContent = `window.kinby = { data: ${json} };\n${BRIDGE}`
  drawn.head.prepend(policy, theme, base, bridge)
  drawn.documentElement.classList.toggle("dark", dark)
  return `<!doctype html>\n${drawn.documentElement.outerHTML}`
}

/** The error a view's frame posts, or undefined for any other message. */
export function postedError(message: unknown): string | undefined {
  if (typeof message !== "object" || message === null) return undefined
  if (!("kinby" in message) || message.kinby !== "error" || !("message" in message))
    return undefined
  return typeof message.message === "string" ? message.message : undefined
}

/** The height a view's frame posts, or undefined for any other message. */
export function postedHeight(message: unknown): number | undefined {
  if (typeof message !== "object" || message === null) return undefined
  if (!("kinby" in message) || message.kinby !== "height" || !("height" in message))
    return undefined
  const { height } = message
  return typeof height === "number" && Number.isFinite(height) && height >= 0 ? height : undefined
}

/** Whether a view's frame asks to be drawn again with the latest data. */
export function asksRedraw(message: unknown): boolean {
  return (
    typeof message === "object" &&
    message !== null &&
    "kinby" in message &&
    message.kinby === "redraw"
  )
}
