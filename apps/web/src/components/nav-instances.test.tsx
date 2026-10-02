import type { InstanceSummary } from "@kinby/contract"
import { instanceSummary } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { NavInstances } from "@/components/nav-instances"
import { SidebarProvider } from "@/components/ui/sidebar"

/** Ada's row in the sidebar, listing only her and no instance selected. */
function adaRow(fields: Partial<InstanceSummary>) {
  const ada = instanceSummary({ instance_id: "instance-1", persona_name: "Ada", ...fields })
  render(
    <SidebarProvider>
      <NavInstances instances={[ada]} selected={undefined} creating={false} threads={null} />
    </SidebarProvider>,
  )
  const link = screen.getByRole("link", { name: "Ada" })
  const row = screen.getAllByRole("listitem").find((item) => item.contains(link))
  return within(row!)
}

describe("an instance's badge in the sidebar", () => {
  it.each([
    ["stopped", {}, "stopped"],
    ["failed", { detail: "exited (1)" }, "stopped"],
    ["created", {}, "stopped"],
    ["missing", {}, "stopped"],
    ["starting", {}, "starting"],
    ["unavailable", {}, "unavailable"],
  ] as const)("reads a stopped instance whose process is %s as %s", (process, fields, shown) => {
    const row = adaRow({ intended_state: "stopped", process, ...fields })

    expect(row.getByText(shown)).toBeDefined()
  })

  it.each([
    ["failed", { process: "failed", detail: "exited (1)" }, "failed"],
    ["created", { process: "created" }, "created"],
    ["missing", { process: "missing" }, "missing"],
    ["restarting", { process: "starting", detail: "restarting" }, "restarting"],
    ["running", { process: "running" }, "running"],
  ] as const)("reads an instance meant to run that is %s as %s", (_, fields, shown) => {
    const row = adaRow({ intended_state: "running", ...fields })

    expect(row.getByText(shown)).toBeDefined()
  })
})
