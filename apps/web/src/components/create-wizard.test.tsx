import type { OperationGetResult, PackageDescription } from "@kinby/contract"
import { type Answers, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { CreateWizard } from "@/components/create-wizard"

const vanilla: PackageDescription = {
  display_name: "Vanilla",
  description: "kinby's built-in defaults, with no package.",
  icon: "sparkles",
  version: "0.1.0",
  setup_fields: [
    {
      name: "model",
      label: "Model",
      description: "The model the instance calls.",
      kind: "config",
      type: "text",
      required: true,
    },
    {
      name: "api_key",
      label: "API key",
      description: "The key your model provider issued.",
      kind: "secret",
      type: "text",
      required: true,
    },
    {
      name: "behavior_prompt",
      label: "Behavior prompt",
      description: "Instructions the instance follows in every turn.",
      kind: "config",
      type: "multiline",
      required: false,
    },
  ],
}

function preparation(fields: Partial<OperationGetResult>): Answers {
  return {
    "image.prepare": () => ({ operation_id: "op-1" }),
    "operation.get": () => ({
      operation_id: "op-1",
      instance_id: null,
      kind: "prepare",
      state: "running",
      detail: "",
      steps: [],
      ...fields,
    }),
    "package.describe": () => vanilla,
  }
}

async function pickVanilla(answers: Answers) {
  const clock = fakeClock()
  render(<CreateWizard caller={stubCaller(answers)} clock={clock} />)
  await userEvent.setup().click(screen.getByRole("button", { name: "Prepare vanilla" }))
  await act(() => clock.advance(0))
}

const step = (name: string) => within(screen.getByRole("listitem", { name }))

describe("the create wizard's package step", () => {
  it("prepares vanilla when it is picked and shows the step running", async () => {
    await pickVanilla(
      preparation({
        steps: [
          {
            name: "image",
            state: "running",
            detail: "Building the image, or reusing the one prepared.",
          },
        ],
      }),
    )

    expect(
      step("image").getByText("Building the image, or reusing the one prepared."),
    ).toBeDefined()
    expect(step("image").getByText("Running")).toBeDefined()
    expect(screen.getByRole("button", { name: "Prepare vanilla" }).hasAttribute("disabled")).toBe(
      true,
    )
  })

  it("lists the fields the prepared image declares", async () => {
    await pickVanilla(
      preparation({
        state: "succeeded",
        steps: [
          { name: "image", state: "succeeded", detail: "Building the image." },
          { name: "describe", state: "succeeded", detail: "Reading what it declares." },
        ],
      }),
    )

    const fields = within(await screen.findByRole("list", { name: "Setup fields" }))
    const model = within(fields.getByRole("listitem", { name: "Model" }))
    const apiKey = within(fields.getByRole("listitem", { name: "API key" }))
    const prompt = within(fields.getByRole("listitem", { name: "Behavior prompt" }))
    expect(model.getByText("Configuration")).toBeDefined()
    expect(model.queryByText("Optional")).toBeNull()
    expect(apiKey.getByText("Secret")).toBeDefined()
    expect(prompt.getByText("Optional")).toBeDefined()
    expect(prompt.getByText("Instructions the instance follows in every turn.")).toBeDefined()
  })

  it("marks the step that failed with what went wrong", async () => {
    await pickVanilla(
      preparation({
        state: "failed",
        detail: 'Executable "claude" is not on PATH.',
        steps: [
          { name: "image", state: "succeeded", detail: "Building the image." },
          { name: "describe", state: "failed", detail: 'Executable "claude" is not on PATH.' },
        ],
      }),
    )

    expect(step("describe").getByText("Failed")).toBeDefined()
    expect(step("describe").getByText('Executable "claude" is not on PATH.')).toBeDefined()
    expect(step("image").getByText("Done")).toBeDefined()
    expect(screen.queryByRole("list", { name: "Setup fields" })).toBeNull()
  })

  it("says why when the preparation could not start", async () => {
    await pickVanilla({})

    expect((await screen.findByRole("alert")).textContent).toContain(
      "The stub has no answer for image.prepare.",
    )
  })
})
