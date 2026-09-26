// PROTOTYPE, throwaway. In-memory config state for the stub instance. Every action logs the call
// it would make; `missing: true` marks a call the v1 contract does not have yet.
import { useRef, useState } from "react"

import {
  CONFIG_FILES,
  type ConfigFile,
  FILES,
  INSTANCE,
  LOGINS,
  ROUTINES,
  type Routine,
  SECRETS,
  type Secret,
  SKILLS,
  type Skill,
  TOOLS,
} from "./stub"

export type Problem = { message: string; line?: number }
export type Call = { id: number; method: string; params: string; result: string; missing: boolean }
export type WriteResult =
  | { kind: "ok"; hash: string; applies: string; restart_required: string[] }
  | { kind: "problems"; problems: Problem[] }
  | { kind: "conflict"; current: string; hash: string }
export type Step = {
  name: string
  state: "done" | "running" | "failed" | "waiting"
  detail?: string
}
export type Operation = {
  id: string
  kind: string
  steps: Step[]
  state: "running" | "done" | "failed"
}

type FileState = { content: string; version: number }

const hashOf = (version: number) => `sha256:${(0x9a3f + version * 7919).toString(16)}`

function lineOf(content: string, needle: RegExp): number | undefined {
  const index = content.split("\n").findIndex((line) => needle.test(line))
  return index === -1 ? undefined : index + 1
}

function valueOf(content: string, key: string): string | undefined {
  return content.match(new RegExp(`^${key}\\s*=\\s*"([^"]*)"`, "m"))?.[1]
}

function validate(
  file: ConfigFile,
  content: string,
): { problems: Problem[]; applies: string; restart: string[] } {
  const problems: Problem[] = []
  if (file === "kinby.toml") {
    const original = FILES["kinby.toml"]
    for (const key of ["listen", "state_dir"]) {
      if (valueOf(content, key) !== valueOf(original, key))
        problems.push({
          message: `${key === "listen" ? "serve.listen" : key} is set by the hub and can't be changed here.`,
          line: lineOf(content, new RegExp(`^${key}\\s*=`)),
        })
    }
    for (const key of ["main", "recap"]) {
      const value = valueOf(content, key)
      if (value !== undefined && !value.includes(":"))
        problems.push({
          message: `models.${key} must be provider:model, got "${value}".`,
          line: lineOf(content, new RegExp(`^${key}\\s*=`)),
        })
    }
  }
  if (file === "permissions.toml") {
    const modes = ["read-only", "ask", "auto", "full-access"]
    for (const key of ["mode", "ceiling"]) {
      const value = valueOf(content, key)
      if (value !== undefined && !modes.includes(value))
        problems.push({
          message: `${key} must be one of ${modes.join(", ")}.`,
          line: lineOf(content, new RegExp(`^${key}\\s*=`)),
        })
    }
  }
  if (file === "package.yaml")
    return { problems, applies: "after recreate", restart: ["package.yaml"] }
  if (file === "RECAP.md") return { problems, applies: "at the next recap", restart: [] }
  return { problems, applies: "at the next turn", restart: [] }
}

const PREFLIGHT_PROBLEMS: Problem[] = [
  {
    message:
      "routines/dependency-bump/ROUTINE.md: unknown frontmatter key 'catch-up' (did you mean catch_up?)",
  },
  {
    message:
      "package.yaml: skills.review names 'adversarial-review', which coder 0.5.0 no longer ships",
  },
]

export function useConfig() {
  const [files, setFiles] = useState<Record<ConfigFile, FileState>>(
    () =>
      Object.fromEntries(CONFIG_FILES.map((f) => [f, { content: FILES[f], version: 1 }])) as Record<
        ConfigFile,
        FileState
      >,
  )
  const [routines, setRoutines] = useState<Routine[]>(ROUTINES)
  const [skills, setSkills] = useState<Skill[]>(SKILLS)
  const [secrets, setSecrets] = useState<Secret[]>(SECRETS)
  const [logins, setLogins] = useState(LOGINS)
  const [pending, setPending] = useState<string[]>([])
  const [operation, setOperation] = useState<Operation | null>(null)
  const [revision, setRevision] = useState(INSTANCE.revision)
  const [preflightFails, setPreflightFails] = useState(false)
  const [calls, setCalls] = useState<Call[]>([])
  const nextId = useRef(1)

  const log = (method: string, params: unknown, result: unknown, missing: boolean) =>
    setCalls((c) => [
      {
        id: nextId.current++,
        method,
        params: JSON.stringify(params),
        result: JSON.stringify(result),
        missing,
      },
      ...c,
    ])

  const read = (file: ConfigFile) => {
    const state = files[file]
    const result = { content: `${state.content.length} chars`, hash: hashOf(state.version) }
    log("config.read", { file }, result, true)
    return { content: state.content, hash: hashOf(state.version) }
  }

  const write = (file: ConfigFile, content: string, hash: string): WriteResult => {
    const state = files[file]
    let result: WriteResult
    if (hash !== hashOf(state.version)) {
      result = { kind: "conflict", current: state.content, hash: hashOf(state.version) }
    } else {
      const { problems, applies, restart } = validate(file, content)
      if (problems.length > 0) {
        result = { kind: "problems", problems }
      } else {
        setFiles((f) => ({ ...f, [file]: { content, version: state.version + 1 } }))
        if (restart.length > 0) setPending((p) => [...new Set([...p, ...restart])])
        result = { kind: "ok", hash: hashOf(state.version + 1), applies, restart_required: restart }
        log("event config.changed", { file, by: "user (app)" }, "appended", true)
      }
    }
    log(
      "config.write",
      { file, hash, content: `${content.length} chars` },
      result.kind === "conflict" ? { error: "changed_since_read", hash: result.hash } : result,
      true,
    )
    return result
  }

  // Simulates the agent (through routine_write and friends) or the failure policy writing in between.
  const agentEdit = (file: ConfigFile) => {
    setFiles((f) => ({
      ...f,
      [file]: {
        content: `${f[file].content}\n# edited by the agent in thread "tidy prompts"\n`,
        version: f[file].version + 1,
      },
    }))
    log("(agent) tool.call", { file }, "file changed on disk", false)
  }

  const setRoutineEnabled = (name: string, enabled: boolean) => {
    setRoutines((rs) =>
      rs.map((r) =>
        r.name === name
          ? {
              ...r,
              enabled,
              failure_count: enabled ? 0 : r.failure_count,
              content: r.content.replace(/enabled: \w+/, `enabled: ${enabled}`),
              next_run: enabled && r.schedule ? "Sun 03:00" : null,
            }
          : r,
      ),
    )
    log("routine.set_enabled", { name, enabled }, { hash: "sha256:…" }, true)
  }

  const runRoutine = (name: string) => log("routine.run", { name }, { accepted: true }, false)

  const writeRoutine = (name: string, content: string): WriteResult => {
    if (!/^---\n[\s\S]*\n---\n/.test(content)) {
      const result: WriteResult = {
        kind: "problems",
        problems: [{ message: "ROUTINE.md needs frontmatter between --- lines.", line: 1 }],
      }
      log("routine.write", { name }, result, true)
      return result
    }
    setRoutines((rs) =>
      rs.map((r) =>
        r.name === name ? { ...r, content, enabled: /enabled: true/.test(content) } : r,
      ),
    )
    const result: WriteResult = {
      kind: "ok",
      hash: "sha256:…",
      applies: "at the next scheduler pass",
      restart_required: [],
    }
    log("routine.write", { name, hash: "sha256:…" }, result, true)
    return result
  }

  const customizeSkill = (skill: Skill) => {
    setSkills((s) => [{ ...skill, tier: "instance" }, ...s])
    log(
      "skill.write",
      { name: skill.name, from: skill.tier },
      { hash: "sha256:…", shadows: skill.tier },
      true,
    )
  }

  const removeCustomization = (name: string) => {
    setSkills((s) => s.filter((k) => !(k.name === name && k.tier === "instance")))
    log("skill.delete", { name }, { restored: "package" }, true)
  }

  const listSkills = () =>
    log("skill.list", {}, { skills: skills.length, fields: "name, tier, shadowed_by" }, true)
  const listTools = () =>
    log("tool.list", {}, { tools: TOOLS.length, fields: "name, source, writes, rule" }, true)
  const status = () =>
    log(
      "instance.status",
      { instance_id: INSTANCE.id },
      { secrets: "[{name, required, is_set}]", setup: "…" },
      true,
    )

  const setSecret = (name: string) => {
    setSecrets((s) => s.map((x) => (x.name === name ? { ...x, is_set: true } : x)))
    setPending((p) => [...new Set([...p, `secret ${name}`])])
    log(
      "instance.secrets.set",
      { instance_id: INSTANCE.id, secrets: { [name]: "•••" } },
      { names: [name], requires_recreate: true },
      false,
    )
  }

  const runOperation = (
    kind: string,
    steps: string[],
    onDone: () => void,
    failAt?: { step: number; detail: string },
  ) => {
    const id = `op_${Math.random().toString(36).slice(2, 6)}`
    const initial: Operation = {
      id,
      kind,
      state: "running",
      steps: steps.map((name) => ({ name, state: "waiting" })),
    }
    setOperation(initial)
    steps.forEach((_, index) => {
      setTimeout(
        () => {
          setOperation((op) => {
            if (op === null || op.id !== id || op.state !== "running") return op
            if (failAt?.step === index) {
              return {
                ...op,
                state: "failed",
                steps: op.steps.map((s, i) =>
                  i === index ? { ...s, state: "failed", detail: failAt.detail } : s,
                ),
              }
            }
            const next = op.steps.map((s, i) =>
              i < index
                ? { ...s, state: "done" as const }
                : i === index
                  ? { ...s, state: "running" as const }
                  : s,
            )
            return { ...op, steps: next }
          })
        },
        700 * (index + 1),
      )
    })
    setTimeout(
      () => {
        setOperation((op) => {
          if (op === null || op.id !== id || op.state !== "running") return op
          onDone()
          return { ...op, state: "done", steps: op.steps.map((s) => ({ ...s, state: "done" })) }
        })
      },
      700 * (steps.length + 1),
    )
    return id
  }

  const recreate = () => {
    const id = runOperation(
      "instance.recreate",
      ["drain", "stop", "create container", "start", "health"],
      () => setPending([]),
    )
    log("instance.recreate", { instance_id: INSTANCE.id }, { operation_id: id }, false)
  }

  const update = (target: string) => {
    const id = runOperation(
      "instance.update",
      ["prepare image", "preflight", "drain", "replace container", "health"],
      () => setRevision(target),
      preflightFails
        ? { step: 1, detail: "candidate rejected the current configuration" }
        : undefined,
    )
    log(
      "instance.update",
      { instance_id: INSTANCE.id, revision: target },
      { operation_id: id },
      false,
    )
  }

  const login = (name: string) => {
    const id = `op_${Math.random().toString(36).slice(2, 6)}`
    setOperation({
      id,
      kind: `login ${name}`,
      state: "running",
      steps: [
        { name: "sign in", state: "running", detail: "https://claude.ai/device · code WXYZ-4821" },
      ],
    })
    log(
      "instance.login.start",
      { instance_id: INSTANCE.id, login: name },
      { operation_id: id },
      true,
    )
  }

  const finishLogin = (name: string) => {
    setLogins((l) => l.map((x) => (x.name === name ? { ...x, status: "complete" as const } : x)))
    setOperation((op) =>
      op ? { ...op, state: "done", steps: op.steps.map((s) => ({ ...s, state: "done" })) } : op,
    )
    log("operation.get", { operation_id: operation?.id }, { state: "done" }, false)
  }

  const permissionRule = (tool: string): string => {
    const match = files["permissions.toml"].content.match(
      new RegExp(`^${tool}\\s*=\\s*"([^"]*)"`, "m"),
    )
    return match
      ? `${match[1]} (tools.${tool})`
      : `mode default (${valueOf(files["permissions.toml"].content, "mode") ?? "ask"})`
  }

  const models = () => ({
    main: valueOf(files["kinby.toml"].content, "main") ?? "?",
    recap: valueOf(files["kinby.toml"].content, "recap") ?? "same as main",
  })

  return {
    files,
    read,
    write,
    agentEdit,
    routines,
    setRoutineEnabled,
    runRoutine,
    writeRoutine,
    skills,
    customizeSkill,
    removeCustomization,
    listSkills,
    tools: TOOLS,
    listTools,
    permissionRule,
    models,
    secrets,
    setSecret,
    logins,
    login,
    finishLogin,
    status,
    pending,
    recreate,
    operation,
    revision,
    update,
    preflightFails,
    setPreflightFails,
    preflightProblems: PREFLIGHT_PROBLEMS,
    calls,
    log,
  }
}

export type Config = ReturnType<typeof useConfig>
