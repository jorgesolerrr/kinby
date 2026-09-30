import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { compileFromFile } from "json-schema-to-typescript"
import { expect, expectTypeOf, test } from "vitest"
import type {
  ApiUse,
  Contract,
  EndFrame,
  ErrorFrame,
  Event,
  InstanceListResult,
  ItemFrame,
  OriginUse,
  PermissionMode,
  PlanLimit,
  PlanUse,
  ResultFrame,
  RoutineOrigin,
  RunDelegated,
  SourceRuns,
  ServerFrame,
  StatsBucket,
  StatsGetCommand,
  SubscribedFrame,
  SubscriptionUse,
  ThreadStatus,
  ThreadSummary,
  UsageSource,
  UserOrigin,
} from "./index"

test("the committed types are a fresh generation of the contract schema", async () => {
  const schema = fileURLToPath(
    new URL("../../../docs/schema/contract.schema.json", import.meta.url),
  )
  const committed = await readFile(new URL("contract.ts", import.meta.url), "utf8")

  expect(committed, "regenerate them with `bun run generate`").toBe(await compileFromFile(schema))
})

test("a server frame narrows to one frame on its type", () => {
  const frames: ServerFrame[] = [
    { type: "subscribed", id: "1", head_sequence: 0 },
    { type: "end", id: "1" },
  ]

  for (const frame of frames) {
    switch (frame.type) {
      case "result":
        expectTypeOf(frame).toEqualTypeOf<ResultFrame>()
        break
      case "error":
        expectTypeOf(frame).toEqualTypeOf<ErrorFrame>()
        break
      case "subscribed":
        expectTypeOf(frame).toEqualTypeOf<SubscribedFrame>()
        break
      case "item":
        expectTypeOf(frame).toEqualTypeOf<ItemFrame>()
        break
      case "end":
        expectTypeOf(frame).toEqualTypeOf<EndFrame>()
        break
      default:
        expectTypeOf(frame).toBeNever()
    }
  }
})

test("a method's result type follows from its name", () => {
  expectTypeOf<Contract["methods"]["instance.list"]["result"]>().toEqualTypeOf<InstanceListResult>()
})

test("an event payload narrows to a delegated run on its type", () => {
  const narrow = (payload: Event["payload"]) => {
    if (payload.type === "run.delegated") {
      expectTypeOf(payload).toEqualTypeOf<RunDelegated>()
    }
  }

  narrow({ type: "message.delta", text: "" })
})

test("stats.get splits every bucket and its total by subscription source", () => {
  type Stats = Contract["methods"]["stats.get"]["result"]

  expectTypeOf<Stats["buckets"][number]["subscriptions"]>().toEqualTypeOf<SubscriptionUse[]>()
  expectTypeOf<Stats["total"]["subscriptions"]>().toEqualTypeOf<SubscriptionUse[]>()
})

test("stats.get splits every bucket and its total by chat and routine", () => {
  type Stats = Contract["methods"]["stats.get"]["result"]

  expectTypeOf<Stats["buckets"][number]["origins"]>().toEqualTypeOf<OriginUse[]>()
  expectTypeOf<Stats["total"]["origins"]>().toEqualTypeOf<OriginUse[]>()
  expectTypeOf<OriginUse>().toEqualTypeOf<{
    origin: "user" | "routine"
    routine: string | null
    turns: number
    no_work: number
    failed: number
    cost: number | null
    runs: SourceRuns[]
  }>()
  expectTypeOf<SourceRuns>().toEqualTypeOf<{ usage_source: UsageSource; runs: number }>()
})

test("stats.get counts each subscription source's runs per plan window, and its active limits", () => {
  type Stats = Contract["methods"]["stats.get"]["result"]

  expectTypeOf<Stats["plan_use"]>().toEqualTypeOf<PlanUse[]>()
  expectTypeOf<PlanUse>().toEqualTypeOf<{
    usage_source: UsageSource
    duration_seconds: number
    runs: number
  }>()
  expectTypeOf<Stats["limits"]>().toEqualTypeOf<PlanLimit[]>()
})

test("stats.get carries each turn's origin, or null when the log has no start for it", () => {
  type Stats = Contract["methods"]["stats.get"]["result"]

  expectTypeOf<Stats["records"][number]["origin"]>().toEqualTypeOf<
    UserOrigin | RoutineOrigin | null
  >()
})

test("stats.summary takes stats.get's range and names each instance it counted or left out", () => {
  type Summary = Contract["methods"]["stats.summary"]

  expectTypeOf<Summary["command"]>().toEqualTypeOf<StatsGetCommand>()
  expectTypeOf<Summary["result"]["buckets"]>().toEqualTypeOf<Record<string, StatsBucket[]>>()
  expectTypeOf<Summary["result"]["api"]>().toEqualTypeOf<ApiUse>()
  expectTypeOf<Summary["result"]["subscriptions"]>().toEqualTypeOf<SubscriptionUse[]>()
  expectTypeOf<Summary["result"]["plan_use"]>().toEqualTypeOf<PlanUse[]>()
  expectTypeOf<Summary["result"]["limits"]>().toEqualTypeOf<PlanLimit[]>()
  expectTypeOf<Summary["result"]["skipped"]>().toEqualTypeOf<string[]>()
  expectTypeOf<Summary["result"]["unreachable"]>().toEqualTypeOf<string[]>()
})

test("thread.list reads each thread's status and last activity", () => {
  type Thread = Contract["methods"]["thread.list"]["result"]["threads"][number]

  expectTypeOf<Thread["status"]>().toEqualTypeOf<ThreadStatus>()
  expectTypeOf<ThreadStatus>().toEqualTypeOf<"idle" | "running" | "awaiting_approval" | "failed">()
  expectTypeOf<Thread["last_activity_at"]>().toEqualTypeOf<string>()
})

test("thread.list reads each thread's mode and the instance's ceiling", () => {
  type Listed = Contract["methods"]["thread.list"]["result"]

  expectTypeOf<Listed["ceiling"]>().toEqualTypeOf<PermissionMode>()
  expectTypeOf<Listed["threads"][number]["mode"]>().toEqualTypeOf<PermissionMode>()
  expectTypeOf<Listed["threads"][number]["mode_pinned"]>().toEqualTypeOf<boolean>()
})

test("thread.rename takes a title and returns the thread's summary", () => {
  type Rename = Contract["methods"]["thread.rename"]

  expectTypeOf<Rename["command"]>().toEqualTypeOf<{ thread_id: string; title: string }>()
  expectTypeOf<Rename["result"]>().toEqualTypeOf<ThreadSummary>()
})

test("thread.approval.respond takes a decision and, on a denial, a reason", () => {
  type Respond = Contract["methods"]["thread.approval.respond"]["command"]
  type Gated = Extract<Event["payload"], { type: "tool.gated" }>

  expectTypeOf<Respond["decision"]>().toEqualTypeOf<"approve" | "deny">()
  expectTypeOf<Respond["reason"]>().toEqualTypeOf<string | null | undefined>()
  expectTypeOf<Gated["reason"]>().toEqualTypeOf<string | null | undefined>()
})
