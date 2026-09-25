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
  PlanLimit,
  PlanWindow,
  ResultFrame,
  RunDelegated,
  ServerFrame,
  StatsBucket,
  StatsGetCommand,
  SubscribedFrame,
  SubscriptionUse,
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

test("stats.get names each subscription source's plan windows and active limits", () => {
  type Stats = Contract["methods"]["stats.get"]["result"]

  expectTypeOf<Stats["plan_windows"]>().toEqualTypeOf<PlanWindow[]>()
  expectTypeOf<Stats["limits"]>().toEqualTypeOf<PlanLimit[]>()
})

test("stats.summary takes stats.get's range and names each instance it counted or left out", () => {
  type Summary = Contract["methods"]["stats.summary"]

  expectTypeOf<Summary["command"]>().toEqualTypeOf<StatsGetCommand>()
  expectTypeOf<Summary["result"]["buckets"]>().toEqualTypeOf<Record<string, StatsBucket[]>>()
  expectTypeOf<Summary["result"]["api"]>().toEqualTypeOf<ApiUse>()
  expectTypeOf<Summary["result"]["subscriptions"]>().toEqualTypeOf<SubscriptionUse[]>()
  expectTypeOf<Summary["result"]["limits"]>().toEqualTypeOf<PlanLimit[]>()
  expectTypeOf<Summary["result"]["skipped"]>().toEqualTypeOf<string[]>()
  expectTypeOf<Summary["result"]["unreachable"]>().toEqualTypeOf<string[]>()
})
