import { stubCaller } from "@kinby/contract/testing"
import { describe, expect, it } from "vitest"
import { threadSummary } from "@/components/thread-testing"
import { threadList } from "@/lib/thread-list"

describe("threadList", () => {
  it("lists every thread in the set, past the 50 a thread.list page holds by default", async () => {
    const threads = Array.from({ length: 51 }, (_, index) => threadSummary({ id: `t${index}` }))
    const client = stubCaller({
      "thread.list": ({ limit = 50 }) => ({
        threads: threads.slice(0, limit ?? undefined),
        ceiling: "full-access",
        cursor: null,
      }),
    })
    const sidebar = threadList(client, "sidebar")

    await sidebar.list()

    expect(sidebar.view()?.threads).toHaveLength(51)
  })
})
