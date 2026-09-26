// PROTOTYPE, throwaway. In-memory state for the stub instance's memory. Every action logs the call
// it would make; `missing: true` marks a call the v1 contract does not have yet (all of them: the
// v1 memory facade has no contract methods).
import { useRef, useState } from "react"

import { AGENT_WRITES, type Kind, type MemoryNode, NODES, PROFILE } from "./stub"

export type Call = { id: number; method: string; params: string; result: string; missing: boolean }
export type Filters = {
  query: string
  kind: Kind | "all"
  subject: string | null
  after: string
  before: string
}
export type SaveResult = { kind: "ok" } | { kind: "conflict"; current: string }

export const NO_FILTERS: Filters = { query: "", kind: "all", subject: null, after: "", before: "" }
const PAGE = 5

const hashOf = (version: number) => `sha256:${(0x51c0 + version * 7919).toString(16)}`
export const tokensOf = (text: string) => Math.ceil(text.length / 4)

const today = () => "2026-09-26"
const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, 40)
const newNode = (description: string) =>
  `${today()}-${Math.random().toString(16).slice(2, 10)}-${slug(description)}`

// Same matching as `recall`: every term in description or subjects, inclusive dates.
function matches(n: MemoryNode, f: Filters) {
  if (n.tombstone) return false
  if (f.kind !== "all" && n.kind !== f.kind) return false
  if (f.subject && !n.subjects.includes(f.subject)) return false
  if (f.after && n.date < f.after) return false
  if (f.before && n.date > f.before) return false
  const text = [n.description, ...n.subjects].join(" ").toLowerCase()
  return f.query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((t) => text.includes(t))
}

const newestFirst = (a: MemoryNode, b: MemoryNode) =>
  b.date.localeCompare(a.date) || b.node.localeCompare(a.node)

export function useMemory() {
  const nextId = useRef(1)
  const [calls, setCalls] = useState<Call[]>([])
  const [running, setRunning] = useState(true)
  // What is on disk in the instance, and what the page loaded from it.
  const [store, setStore] = useState<MemoryNode[]>(NODES)
  const [loaded, setLoaded] = useState<MemoryNode[]>(NODES)
  const [profileStore, setProfileStore] = useState({ text: PROFILE, version: 1 })
  const [profile, setProfile] = useState({ text: PROFILE, hash: hashOf(1) })
  const [pending, setPending] = useState(0)

  const log = (method: string, params: unknown, result: string) =>
    setCalls((c) => [
      { id: nextId.current++, method, params: JSON.stringify(params), result, missing: true },
      ...c,
    ])

  const list = (f: Filters, pages: number) => {
    const all = loaded.filter((n) => matches(n, f)).sort(newestFirst)
    const items = all.slice(0, pages * PAGE)
    return { items, more: all.length > items.length }
  }

  const subjects = () => {
    const counts = new Map<string, number>()
    for (const n of loaded)
      if (!n.tombstone) for (const s of n.subjects) counts.set(s, (counts.get(s) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }

  const logList = (f: Filters, pages: number) => {
    const { items, more } = list(f, pages)
    const params = Object.fromEntries(
      Object.entries(f).filter(([k, v]) => v && !(k === "kind" && v === "all")),
    )
    log(
      "memory.list",
      pages > 1 ? { ...params, cursor: `c${pages - 1}` } : params,
      `${items.length} items${more ? ", cursor" : ""}`,
    )
  }

  const open = (id: string) => {
    const n = loaded.find((x) => x.node === id)
    log("memory.open", { node: id }, n ? n.kind : "not found")
    return n
  }

  const write = (next: MemoryNode[]) => {
    setStore(next)
    setLoaded(next)
  }

  const add = (fact: { description: string; subjects: string[]; body: string }) => {
    const id = newNode(fact.description)
    write([
      ...store,
      { ...fact, node: id, kind: "fact", date: today(), source: "user", tombstone: false },
    ])
    log("memory.add", fact, `{node: "${id}"}`)
    return id
  }

  const correct = (
    old: string,
    fact: { description: string; subjects: string[]; body: string },
  ) => {
    const id = newNode(fact.description)
    write([
      ...store.map((n) => (n.node === old ? { ...n, tombstone: true } : n)),
      { ...fact, node: id, kind: "fact", date: today(), source: "user", tombstone: false },
    ])
    log("memory.correct", { node: old, ...fact }, `{node: "${id}"}, old tombstoned`)
    return id
  }

  const forget = (id: string) => {
    write(store.map((n) => (n.node === id ? { ...n, tombstone: true } : n)))
    log("memory.forget", { node: id }, "tombstoned")
  }

  const refresh = (f: Filters) => {
    setLoaded(store)
    setPending(0)
    setProfile({ text: profileStore.text, hash: hashOf(profileStore.version) })
    log("profile.get", {}, hashOf(profileStore.version))
    const all = store.filter((n) => matches(n, f))
    log("memory.list", {}, `${Math.min(all.length, PAGE)} items`)
  }

  const saveProfile = (text: string): SaveResult => {
    if (profile.hash !== hashOf(profileStore.version)) {
      log("profile.set", { hash: profile.hash }, "stale hash: changed since you opened it")
      return { kind: "conflict", current: profileStore.text }
    }
    const version = profileStore.version + 1
    setProfileStore({ text, version })
    setProfile({ text, hash: hashOf(version) })
    log("profile.set", { hash: profile.hash }, `ok ${hashOf(version)}, config.changed appended`)
    return { kind: "ok" }
  }

  const loadTheirs = () => {
    setProfile({ text: profileStore.text, hash: hashOf(profileStore.version) })
    log("profile.get", {}, hashOf(profileStore.version))
  }

  // Simulated outside writes: the agent remembering mid-turn, someone editing profile.md on disk.
  const agentRemembers = () => {
    const next = AGENT_WRITES.find((w) => !store.some((n) => n.node === w.node))
    if (!next) return
    setStore([...store, next])
    setPending((p) => p + 1)
  }
  const diskEditsProfile = () =>
    setProfileStore((p) => ({
      text: `${p.text}- Edited on disk at ${new Date().toLocaleTimeString()}.\n`,
      version: p.version + 1,
    }))

  return {
    calls,
    running,
    setRunning,
    list,
    logList,
    subjects,
    open,
    add,
    correct,
    forget,
    refresh,
    pending,
    profile,
    saveProfile,
    loadTheirs,
    agentRemembers,
    diskEditsProfile,
  }
}

export type Memory = ReturnType<typeof useMemory>
