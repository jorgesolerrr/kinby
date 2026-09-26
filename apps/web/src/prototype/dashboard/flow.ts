// PROTOTYPE, throwaway. Dashboard state and the contract calls each view would make.
import { useEffect, useMemo, useState } from "react"

import {
  bucketSize,
  INSTANCES,
  NOW,
  planWindows,
  since,
  statsGet,
  statsSummary,
  type Range,
} from "./stub"

export type Page = { kind: "usage" } | { kind: "stats"; instanceId: string }

export function useDashboard() {
  const [page, setPage] = useState<Page>({ kind: "stats", instanceId: "coder" })
  const [range, setRange] = useState<Range>(7)
  const [bucket, setBucket] = useState<string | null>(null)
  const [origin, setOrigin] = useState<string | null>(null)
  const [calls, setCalls] = useState<string[]>([])
  const [loads, setLoads] = useState(0)

  const by = bucketSize(range)
  const from = since(range)
  const iso = (d: Date) => d.toISOString().slice(0, 16) + "Z"

  // What the page asks for on open, on a range change, on focus, and on refresh.
  useEffect(() => {
    const at = new Date().toLocaleTimeString()
    const made =
      page.kind === "usage"
        ? [
            `stats.summary { since: ${iso(from)}, by: ${by} }`,
            `stats.summary { since: ${iso(new Date(NOW.getTime() - 5 * 3_600_000))} }  · 5h plan window`,
            `stats.summary { since: ${iso(new Date(NOW.getTime() - 7 * 86_400_000))} }  · 7d plan window`,
            `instance.list {}`,
          ]
        : [
            `[relay ${page.instanceId}] stats.get { since: ${iso(from)}, by: ${by} }`,
            `[relay ${page.instanceId}] stats.get { since: now − 5h }  · 5h plan window`,
            `[relay ${page.instanceId}] stats.get { since: now − 7d }  · 7d plan window`,
            `[relay ${page.instanceId}] routine.list {}`,
          ]
    setCalls((prev) => [...made.map((c) => `${at}  ${c}`), ...prev].slice(0, 24))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, range, loads])

  useEffect(() => {
    const onFocus = () => setLoads((n) => n + 1)
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [])

  const instance = page.kind === "stats" ? INSTANCES.find((i) => i.id === page.instanceId) : undefined

  const stats = useMemo(() => (instance ? statsGet(instance, from, by) : null), [instance, range])
  const instancePlans = useMemo(
    () => (instance ? planWindows((f) => statsGet(instance, f, "day").total.subscriptions) : null),
    [instance],
  )
  const hub = useMemo(() => (page.kind === "usage" ? statsSummary(from, by) : null), [page, range])
  const hubPlans = useMemo(() => planWindows((f) => statsSummary(f, "day").subscriptions), [])

  return {
    page,
    open: (next: Page) => {
      setPage(next)
      setBucket(null)
      setOrigin(null)
    },
    range,
    setRange: (next: Range) => {
      setRange(next)
      setBucket(null)
    },
    by,
    bucket,
    setBucket,
    origin,
    setOrigin,
    refresh: () => setLoads((n) => n + 1),
    calls,
    instance,
    stats,
    plans: page.kind === "usage" ? hubPlans : instancePlans,
    limits: page.kind === "usage" ? (hub?.limits ?? []) : (stats?.limits ?? []),
    hub,
  }
}

export type Dashboard = ReturnType<typeof useDashboard>
