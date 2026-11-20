/** A send back to an earlier step, at most `max` times along this route. */
export type SendBack = { back: string; max: number }
export type Retry = { retry: number }
/** A step of a `factory.yaml` as the file declares it. The hub checked it. A part the file
 * declares as null is absent. */
export type FactoryStep = {
  id: string
  kind: string
  description?: string | null
  off?: boolean
  engine?: string
  model?: string | null
  effort?: string | null
  timeout?: string | null
  resume?: string | null
  retry?: number | null
  prompt?: string
  check?: string | null
  call?: string
  quiet?: string | null
  signal?: unknown
  requires?: string[]
  results?: Record<string, string>
  outcomes?: Record<string, string | SendBack>
  failed?: SendBack | Retry | null
  [part: string]: unknown
}

/** How a route reads in words: "back to babysit, up to 30 times". */
export function routeText(route: string | SendBack | Retry): string {
  if (typeof route === "string") return route === "next" ? "next step" : route
  const times = (count: number) => `up to ${count} ${count === 1 ? "time" : "times"}`
  return "back" in route
    ? `back to ${route.back}, ${times(route.max)}`
    : `this step again, ${times(route.retry)}`
}
