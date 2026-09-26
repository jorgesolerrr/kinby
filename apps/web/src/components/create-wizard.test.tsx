import { CallError } from "@kinby/contract"
import type { OperationGetResult, PackageDescription } from "@kinby/contract"
import { type Answers, type StubCaller, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent, { type UserEvent } from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

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
    expect(screen.getByRole("button", { name: "Prepare vanilla" }).hasAttribute("disabled")).toBe(
      false,
    )
  })

  it("says why when the preparation could not start", async () => {
    await pickVanilla({})

    expect((await screen.findByRole("alert")).textContent).toContain(
      "The stub has no answer for image.prepare.",
    )
    expect(screen.getByRole("button", { name: "Prepare vanilla" }).hasAttribute("disabled")).toBe(
      false,
    )
  })

  it("prepares vanilla again after the preparation failed", async () => {
    let prepares = 0
    const caller = stubCaller({
      "image.prepare": () => {
        prepares += 1
        return { operation_id: prepares === 1 ? "op-1" : "op-2" }
      },
      "operation.get": () =>
        prepares === 1
          ? {
              operation_id: "op-1",
              instance_id: null,
              kind: "prepare",
              state: "failed",
              detail: "Cannot connect to the Docker daemon.",
              steps: [
                {
                  name: "image",
                  state: "failed",
                  detail: "Cannot connect to the Docker daemon.",
                },
              ],
            }
          : {
              operation_id: "op-2",
              instance_id: null,
              kind: "prepare",
              state: "running",
              detail: "",
              steps: [
                {
                  name: "image",
                  state: "running",
                  detail: "Building the image, or reusing the one prepared.",
                },
              ],
            },
    })
    const clock = fakeClock()
    render(<CreateWizard caller={caller} clock={clock} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Prepare vanilla" }))
    await act(() => clock.advance(0))

    const again = screen.getByRole("button", { name: "Prepare vanilla" })
    expect(again.hasAttribute("disabled")).toBe(false)
    await user.click(again)
    await act(() => clock.advance(0))

    expect(
      step("image").getByText("Building the image, or reusing the one prepared."),
    ).toBeDefined()
    expect(step("image").getByText("Running")).toBeDefined()
    expect(again.hasAttribute("disabled")).toBe(true)
    expect(caller.calls.filter((call) => call.method === "image.prepare")).toEqual([
      { method: "image.prepare", params: { package: null } },
      { method: "image.prepare", params: { package: null } },
    ])
  })
})

const prepared = operationAnswer({
  state: "succeeded",
  steps: [
    { name: "image", state: "succeeded", detail: "Building the image." },
    { name: "describe", state: "succeeded", detail: "Reading what it declares." },
  ],
})

const creating = operationAnswer({
  operation_id: "op-create",
  instance_id: "instance-1",
  kind: "create",
  state: "running",
  steps: [{ name: "image", state: "running", detail: "Preparing the selected image." }],
})

function operationAnswer(fields: Partial<OperationGetResult>): OperationGetResult {
  return {
    operation_id: "op-1",
    instance_id: null,
    kind: "prepare",
    state: "running",
    detail: "",
    steps: [],
    ...fields,
  }
}

/** A hub that prepared vanilla, and answers the create operation's polls with `created`. */
function hub(answers: Answers, created: OperationGetResult = creating) {
  return stubCaller({
    "image.prepare": () => ({ operation_id: "op-1" }),
    "package.describe": () => vanilla,
    "instance.create": () => ({ operation_id: "op-create", instance_id: "instance-1" }),
    "operation.get": ({ operation_id }) => (operation_id === "op-1" ? prepared : created),
    ...answers,
  })
}

async function openWizard(caller: StubCaller, onPublished = () => {}) {
  const clock = fakeClock()
  const user = userEvent.setup()
  render(<CreateWizard caller={caller} clock={clock} onPublished={onPublished} />)
  await user.click(screen.getByRole("button", { name: "Prepare vanilla" }))
  await act(() => clock.advance(0))
  await user.click(await screen.findByRole("button", { name: "Continue" }))
  return { user, clock }
}

async function nameIt(user: UserEvent, name = "Ada") {
  await user.type(screen.getByLabelText("Name"), name)
  await user.click(screen.getByRole("button", { name: "Continue" }))
}

async function fillSetup(user: UserEvent) {
  await user.type(screen.getByLabelText("Model"), "openai:gpt-5")
  await user.type(screen.getByLabelText("API key"), "sk-private")
  await user.type(screen.getByLabelText(/Behavior prompt/), "Answer in haiku.")
}

describe("the create wizard's identity step", () => {
  it("names the instance and draws the avatar picked for it", async () => {
    const { user } = await openWizard(hub({}))

    const next = screen.getByRole("button", { name: "Continue" })
    expect(next.hasAttribute("disabled")).toBe(true)
    await user.type(screen.getByLabelText("Name"), "Ada")
    await user.click(screen.getByRole("button", { name: "Squircle" }))
    await user.click(screen.getByRole("button", { name: "Green" }))

    const preview = screen.getByRole("img", { name: "Avatar of Ada" })
    expect(preview.getAttribute("data-shape")).toBe("squircle")
    expect(preview.querySelector("[data-slot=avatar-fallback]")?.getAttribute("data-variant")).toBe(
      "green",
    )
    expect(next.hasAttribute("disabled")).toBe(false)
  })

  it("starts from a circle in the palette's first color", async () => {
    const { user } = await openWizard(hub({}))

    await user.type(screen.getByLabelText("Name"), "Ada")

    const preview = screen.getByRole("img", { name: "Avatar of Ada" })
    expect(preview.getAttribute("data-shape")).toBe("circle")
    expect(preview.querySelector("[data-slot=avatar-fallback]")?.getAttribute("data-variant")).toBe(
      "blue",
    )
  })
})

describe("the create wizard's setup step", () => {
  it("asks for each declared field, and never shows a secret it was given", async () => {
    const { user } = await openWizard(hub({}))
    await nameIt(user)

    const configuration = within(screen.getByRole("group", { name: "Configuration" }))
    const secrets = within(screen.getByRole("group", { name: "Secrets" }))
    expect(configuration.getByLabelText("Model").tagName).toBe("INPUT")
    expect(configuration.getByLabelText(/Behavior prompt/).tagName).toBe("TEXTAREA")
    expect(configuration.getByText("The model the instance calls.")).toBeDefined()
    expect(secrets.getByLabelText("API key").getAttribute("type")).toBe("password")
  })

  it("creates the instance from the values and the identity", async () => {
    const caller = hub({})
    const { user } = await openWizard(caller)
    await user.type(screen.getByLabelText("Name"), "Ada")
    await user.click(screen.getByRole("button", { name: "Square" }))
    await user.click(screen.getByRole("button", { name: "Continue" }))
    await fillSetup(user)

    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toEqual({
      manifest_id: "Ada",
      persona_name: "Ada",
      model: "openai:gpt-5",
      package: null,
      config: { behavior_prompt: "Answer in haiku." },
      secrets: { api_key: "sk-private" },
      avatar: { shape: "square", color: "blue" },
    })
    const steps = within(await screen.findByRole("list", { name: "Creation steps" }))
    expect(steps.getByText("Preparing the selected image.")).toBeDefined()
  })

  it("marks each field the hub refused, on that field", async () => {
    const caller = hub({
      "instance.create": () => {
        throw new CallError({
          code: "INVALID_SETUP",
          message: "Some setup values are missing or invalid.",
          retryable: false,
          fields: { model: "Name the provider and the model, like openai:gpt-5." },
        })
      },
    })
    const { user } = await openWizard(caller)
    await nameIt(user)
    await fillSetup(user)

    await user.click(screen.getByRole("button", { name: "Create instance" }))

    const model = await screen.findByLabelText("Model")
    expect(model.getAttribute("aria-invalid")).toBe("true")
    expect(
      within(screen.getByRole("group", { name: "Configuration" })).getByRole("alert").textContent,
    ).toBe("Name the provider and the model, like openai:gpt-5.")
    expect(screen.getByLabelText("API key").getAttribute("aria-invalid")).toBeNull()
    expect(screen.queryByRole("list", { name: "Creation steps" })).toBeNull()
  })
})

describe("the create wizard's start step", () => {
  const published = operationAnswer({
    operation_id: "op-create",
    instance_id: "instance-1",
    kind: "create",
    state: "succeeded",
    steps: [{ name: "publish", state: "succeeded", detail: "Instance prepared and stopped." }],
  })

  beforeEach(() => window.history.replaceState(null, "", "/new"))

  async function createdAda(caller: StubCaller, onPublished = () => {}) {
    const { user, clock } = await openWizard(caller, onPublished)
    await nameIt(user)
    await fillSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))
    await act(() => clock.advance(0))
    return { user, clock }
  }

  it("lists the instance once the hub published it", async () => {
    const onPublished = vi.fn()

    await createdAda(hub({}, published), onPublished)

    expect(await screen.findByRole("button", { name: "Start and chat" })).toBeDefined()
    expect(onPublished).toHaveBeenCalledTimes(1)
  })

  it("does not list an instance whose creation failed", async () => {
    const onPublished = vi.fn()
    const failed = operationAnswer({
      operation_id: "op-create",
      kind: "create",
      state: "failed",
      detail: "Cannot connect to the Docker daemon.",
      steps: [{ name: "image", state: "failed", detail: "Cannot connect to the Docker daemon." }],
    })

    await createdAda(hub({}, failed), onPublished)

    expect(await screen.findByRole("button", { name: "Back to setup" })).toBeDefined()
    expect(screen.queryByRole("button", { name: "Start and chat" })).toBeNull()
    expect(onPublished).not.toHaveBeenCalled()
  })

  it("starts the instance and opens it", async () => {
    const caller = hub(
      {
        "instance.start": () => ({ operation_id: "op-start", instance_id: "instance-1" }),
        "operation.get": ({ operation_id }) =>
          operation_id === "op-1"
            ? prepared
            : operation_id === "op-create"
              ? published
              : operationAnswer({ operation_id, kind: "start", state: "succeeded" }),
      },
      published,
    )
    const { user, clock } = await createdAda(caller)

    await user.click(await screen.findByRole("button", { name: "Start and chat" }))
    await act(() => clock.advance(0))

    expect(caller.calls.filter((call) => call.method === "instance.start")).toEqual([
      { method: "instance.start", params: { instance_id: "instance-1" } },
    ])
    expect(window.location.pathname).toBe("/instances/instance-1")
  })

  it("leaves the instance stopped and opens it", async () => {
    const caller = hub({}, published)
    const { user } = await createdAda(caller)

    await user.click(await screen.findByRole("button", { name: "Leave it stopped" }))

    expect(caller.calls.some((call) => call.method === "instance.start")).toBe(false)
    expect(window.location.pathname).toBe("/instances/instance-1")
  })
})
