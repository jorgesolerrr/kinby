import type { View } from "@kinby/contract"

import tokens from "@/tokens.css?raw"

/** Where the hub serves what a view's page may load, such as its base stylesheet. */
const SANDBOX_PATH = "/sandbox/"

/**
 * Runs before the page: it hands the page its data, posts the page's height out, and opens a link
 * to an http, https or mailto address in a new tab. It keeps its names to itself, so a page may
 * use the same ones.
 */
const BRIDGE = `(() => {
  const links = ["http:", "https:", "mailto:"]
  const post = () => parent.postMessage(
    { kinby: "height", height: Math.ceil(document.documentElement.getBoundingClientRect().height) },
    "*",
  )
  new ResizeObserver(post).observe(document.documentElement)
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
 * reaches nothing but the hub's sandbox path, and runs only the scripts carrying this load's nonce:
 * the bridge and each script the page was written with. A handler written as an attribute, or
 * script added later, never runs.
 */
export function viewDocument(
  page: string,
  data: View["data"],
  { dark, origin }: { dark: boolean; origin: string },
): string {
  const nonce = crypto.randomUUID()
  const sandbox = `${origin}${SANDBOX_PATH}`
  const drawn = new DOMParser().parseFromString(page, "text/html")
  for (const script of drawn.querySelectorAll("script")) script.setAttribute("nonce", nonce)

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

/** The height a view's frame posts, or undefined for any other message. */
export function postedHeight(message: unknown): number | undefined {
  if (typeof message !== "object" || message === null) return undefined
  if (!("kinby" in message) || message.kinby !== "height" || !("height" in message))
    return undefined
  const { height } = message
  return typeof height === "number" && Number.isFinite(height) && height >= 0 ? height : undefined
}
