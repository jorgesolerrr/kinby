// PROTOTYPE: pieces both thread variants draw the same way. Throwaway.
import { code } from "@streamdown/code"
import {
  BrainIcon,
  CalendarIcon,
  CloudSunIcon,
  CreditCardIcon,
  GlobeIcon,
  type LucideIcon,
  MailIcon,
  MousePointerClickIcon,
  PencilLineIcon,
  SearchIcon,
  WrenchIcon,
} from "lucide-react"
import { defaultRehypePlugins, Streamdown, type StreamdownProps } from "streamdown"

import type { Tool } from "./mock"

/** The real app's reply settings (thread-panel.tsx), so the prose reads as it will ship. */
const replyOptions = {
  components: { strong: "strong" },
  rehypePlugins: Object.entries(defaultRehypePlugins)
    .filter(([name]) => name !== "raw")
    .map(([, plugin]) => plugin),
  disallowedElements: ["img"],
  plugins: { code },
  linkSafety: { enabled: false },
  className:
    "font-reading text-base leading-7 [&_h1]:text-lg [&_:is(h2,h3,h4)]:text-base [&_:is(h1,h2,h3,h4)]:mt-5 [&_:is(h1,h2,h3,h4)]:font-heading [&_:is(h1,h2,h3,h4)]:font-semibold",
} satisfies StreamdownProps

export function Reply({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <Streamdown {...replyOptions} parseIncompleteMarkdown={streaming}>
      {text}
    </Streamdown>
  )
}

const ICONS: [string, LucideIcon][] = [
  ["memory", BrainIcon],
  ["web_search", SearchIcon],
  ["browser.open", GlobeIcon],
  ["browser.fill", PencilLineIcon],
  ["browser.click", MousePointerClickIcon],
  ["calendar", CalendarIcon],
  ["payments", CreditCardIcon],
  ["email", MailIcon],
  ["weather", CloudSunIcon],
]

export function toolIcon(name: string): LucideIcon {
  return ICONS.find(([prefix]) => name.startsWith(prefix))?.[1] ?? WrenchIcon
}

/** What a run of tools did, in words: "Searched the web, opened 2 pages". */
export function summarize(tools: Tool[]): string {
  const count = (prefix: string) => tools.filter((t) => t.name.startsWith(prefix)).length
  const parts: string[] = []
  const searched = count("web_search")
  const opened = count("browser.open")
  const acted = count("browser.fill") + count("browser.click")
  const recalled = count("memory")
  const other = tools.length - searched - opened - acted - recalled
  if (recalled) parts.push("recalled what it knows")
  if (searched) parts.push("searched the web")
  if (opened) parts.push(opened === 1 ? "opened a page" : `opened ${opened} pages`)
  if (acted) parts.push(acted === 1 ? "used the browser once" : `used the browser ${acted} times`)
  if (other && parts.length === 0)
    parts.push(other === 1 ? `used ${tools[0].name}` : `used ${other} tools`)
  else if (other) parts.push(other === 1 ? "1 more tool" : `${other} more tools`)
  const text = parts.join(", ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function seconds(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** What a running tool is doing, as a present-tense line. */
export function doing(tool: Tool): string {
  if (tool.name === "web_search") return `Searching “${tool.arg}”`
  if (tool.name.startsWith("browser")) return `Working in ${tool.arg}`
  return `${tool.name} ${tool.arg}`
}
