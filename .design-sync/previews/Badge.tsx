import { Badge } from "@kinby/web"
import { CheckIcon, CircleAlertIcon, ClockIcon, ExternalLinkIcon, SparklesIcon } from "lucide-react"

export const Variants = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Badge>Running</Badge>
    <Badge variant="secondary">Library</Badge>
    <Badge variant="destructive">Failed</Badge>
    <Badge variant="success">Signed in</Badge>
    <Badge variant="attention">Needs you</Badge>
    <Badge variant="outline">Instance</Badge>
    <Badge variant="ghost">Shadowed</Badge>
    <Badge variant="link">Docs</Badge>
  </div>
)

export const WithIcons = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Badge variant="success">
      <CheckIcon data-icon="inline-start" />
      Approved
    </Badge>
    <Badge variant="destructive">
      <CircleAlertIcon data-icon="inline-start" />
      Denied by policy
    </Badge>
    <Badge variant="secondary">
      <ClockIcon data-icon="inline-start" />
      Weekdays 8:00
    </Badge>
    <Badge variant="attention">
      <SparklesIcon data-icon="inline-start" />
      New skill
    </Badge>
    <Badge variant="outline">
      Open thread
      <ExternalLinkIcon data-icon="inline-end" />
    </Badge>
  </div>
)

export const InContext = () => (
  <div className="flex w-80 flex-col gap-3 text-sm">
    <div className="flex items-center justify-between">
      <span className="font-medium">inbox-triage</span>
      <Badge variant="secondary">Library</Badge>
    </div>
    <div className="flex items-center justify-between">
      <span className="font-medium">Morning brief</span>
      <Badge variant="success">Ran 2h ago</Badge>
    </div>
    <div className="flex items-center justify-between">
      <span className="font-medium">Claude login</span>
      <Badge variant="attention">Sign in again</Badge>
    </div>
    <div className="flex items-center justify-between">
      <span className="font-medium">Weekly invoices</span>
      <Badge variant="destructive">Failed</Badge>
    </div>
  </div>
)
