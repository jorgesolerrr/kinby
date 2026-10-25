import type {
  ListBinding,
  TextProp,
  View,
  ViewCard,
  ViewComponent,
  ViewComponentId,
  ViewLink,
} from "@kinby/contract"
import { CircleXIcon } from "lucide-react"
import { Component, type ReactNode } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { reason } from "@/lib/operation"

/** What a link in a view may open. Anything else, such as a script, shows as text. */
const LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"])

/** The components by id, and the data their bindings read: the view's, or one list item. */
interface Scope {
  components: ReadonlyMap<ViewComponentId, ViewComponent>
  data: unknown
}

/**
 * A view, rendered from its root with each catalogue component as its shadcn counterpart and each
 * binding read from the view's data. A component that is unknown or fails to render shows an inline
 * error in its place, and the rest of the view still shows.
 */
export function ViewRenderer({ view }: { view: Pick<View, "spec" | "data"> }) {
  const components = new Map(view.spec.components.map((component) => [component.id, component]))
  return <Rendered id="root" scope={{ components, data: view.data }} />
}

function Rendered({ id, scope }: { id: ViewComponentId; scope: Scope }) {
  const component = scope.components.get(id)
  return (
    <ComponentBoundary>
      {component === undefined ? (
        <ComponentError>The view has no component "{id}".</ComponentError>
      ) : (
        <Shown component={component} scope={scope} />
      )}
    </ComponentBoundary>
  )
}

function Shown({ component, scope }: { component: ViewComponent; scope: Scope }) {
  switch (component.component) {
    case "Card":
      return <ViewCardShown card={component} scope={scope} />
    case "Text":
      return <p>{text(component.text, scope)}</p>
    case "Link":
      return <ViewLinkShown link={component} scope={scope} />
  }
  return <Unknown component={component} />
}

function ViewCardShown({ card, scope }: { card: ViewCard; scope: Scope }) {
  const title = text(card.title, scope)
  const description = text(card.description, scope)
  return (
    <Card>
      {(title || description) && (
        <CardHeader>
          {title && <CardTitle>{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent>
        <div className="flex flex-col gap-3">
          <Children named={card.children ?? []} scope={scope} />
        </div>
      </CardContent>
    </Card>
  )
}

/** Children named by id, or a template repeated once per item of an array in the data. */
function Children({ named, scope }: { named: ViewComponentId[] | ListBinding; scope: Scope }) {
  if (Array.isArray(named)) {
    return named.map((id, index) => <Rendered key={`${index} ${id}`} id={id} scope={scope} />)
  }
  const items = resolve(scope.data, named.path)
  if (!Array.isArray(items)) return null
  return (items as unknown[]).map((item, index) => (
    <Rendered key={index} id={named.template} scope={{ ...scope, data: item }} />
  ))
}

function ViewLinkShown({ link, scope }: { link: ViewLink; scope: Scope }) {
  const label = text(link.label, scope)
  const href = text(link.href, scope)
  if (!opens(href)) return <span>{label}</span>
  return (
    <a className="font-medium text-primary underline" href={href} rel="noreferrer" target="_blank">
      {label}
    </a>
  )
}

/** The catalogue grew past this app: the parameter's type says no known component is left. */
function Unknown({ component }: { component: never }) {
  const type = (component as { component?: unknown }).component
  return <ComponentError>This app cannot show a "{String(type)}" component.</ComponentError>
}

function ComponentError({ children }: { children: ReactNode }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>This part of the view could not be shown</AlertTitle>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}

/** One component, or an error in its place when it throws while it renders. */
class ComponentBoundary extends Component<{ children: ReactNode }, { failure?: string }> {
  state: { failure?: string } = {}

  static getDerivedStateFromError(error: unknown) {
    return { failure: reason(error) }
  }

  render() {
    if (this.state.failure === undefined) return this.props.children
    return <ComponentError>{this.state.failure}</ComponentError>
  }
}

/** A text prop's value. A binding to nothing, or to something that is not a value, is empty. */
function text(prop: TextProp | null | undefined, scope: Scope): string {
  if (prop == null || typeof prop === "string") return prop ?? ""
  const value = resolve(scope.data, prop.path)
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : ""
}

/** What an RFC 6901 JSON Pointer names in `data`, if it names anything. */
function resolve(data: unknown, pointer: string): unknown {
  let value = data
  for (const token of pointer.split("/").slice(1)) {
    const key = token.replaceAll("~1", "/").replaceAll("~0", "~")
    if (Array.isArray(value)) value = (value as unknown[])[Number(key)]
    else if (typeof value === "object" && value !== null && Object.hasOwn(value, key)) {
      value = (value as Record<string, unknown>)[key]
    } else return undefined
  }
  return value
}

function opens(href: string): boolean {
  try {
    return LINK_PROTOCOLS.has(new URL(href).protocol)
  } catch {
    return false
  }
}
