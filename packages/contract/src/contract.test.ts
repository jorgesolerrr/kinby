import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { compileFromFile } from "json-schema-to-typescript"
import { expect, expectTypeOf, test } from "vitest"
import type {
  Contract,
  EndFrame,
  ErrorFrame,
  Event,
  InstanceListResult,
  ItemFrame,
  ResultFrame,
  RunDelegated,
  ServerFrame,
  SubscribedFrame,
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
