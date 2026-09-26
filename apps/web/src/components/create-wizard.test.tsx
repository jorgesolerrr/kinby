import { CallError } from "@kinby/contract"
import type {
  CuratedPackage,
  OperationGetResult,
  OperationStep,
  PackageDescription,
  SubscriptionLogin,
} from "@kinby/contract"
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

const coder: CuratedPackage = {
  id: "coder",
  display_name: "Software factory",
  description: "Implements GitHub issues labeled ready-for-agent.",
  icon: "code",
  selection: {
    id: "coder",
    distribution: "kinby-code-factory",
    version: {
      url: "https://github.com/jorgesolerrr/kinby-code-factory",
      sha: "3a68621643e4605d5be72a87f716181ec265ebf5",
    },
    image_recipe: "",
  },
}

const noPackages: Answers = { "package.list": () => ({ packages: [] }) }

function preparation(fields: Partial<OperationGetResult>): Answers {
  return {
    ...noPackages,
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
  it("offers each curated package as a card next to vanilla", async () => {
    const { container } = render(
      <CreateWizard caller={stubCaller({ "package.list": () => ({ packages: [coder] }) })} />,
    )

    const card = await screen.findByRole("listitem", { name: "Software factory" })
    expect(
      within(card).getByText("Implements GitHub issues labeled ready-for-agent."),
    ).toBeDefined()
    expect(within(card).getByText("3a68621")).toBeDefined()
    expect(card.querySelector("svg.lucide-code")).not.toBeNull()
    expect(within(card).getByRole("button", { name: "Prepare Software factory" })).toBeDefined()
    expect(
      within(screen.getByRole("listitem", { name: "Vanilla" })).getByRole("button", {
        name: "Prepare vanilla",
      }),
    ).toBeDefined()
    expect(container.querySelectorAll("[role=alert]")).toHaveLength(0)
  })

  it("prepares a curated package with the selection the list gave it", async () => {
    const clock = fakeClock()
    const caller = stubCaller({
      ...preparation({
        state: "succeeded",
        steps: [{ name: "image", state: "succeeded", detail: "Building the image." }],
      }),
      "package.list": () => ({ packages: [coder] }),
    })
    render(<CreateWizard caller={caller} clock={clock} />)

    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Prepare Software factory" }))
    await act(() => clock.advance(0))

    expect(await screen.findByRole("list", { name: "Setup fields" })).toBeDefined()
    expect(
      caller.calls.filter(
        ({ method }) => method === "image.prepare" || method === "package.describe",
      ),
    ).toEqual([
      { method: "image.prepare", params: { package: coder.selection } },
      { method: "package.describe", params: { package: coder.selection } },
    ])
    expect(
      screen.getByRole("button", { name: "Prepare Software factory" }).hasAttribute("disabled"),
    ).toBe(true)
    expect(screen.getByRole("button", { name: "Prepare vanilla" }).hasAttribute("disabled")).toBe(
      false,
    )
  })

  it("still offers vanilla when the curated list cannot be read", async () => {
    render(<CreateWizard caller={stubCaller({})} />)

    expect((await screen.findByRole("alert")).textContent).toContain(
      "The stub has no answer for package.list.",
    )
    expect(screen.getByRole("button", { name: "Prepare vanilla" })).toBeDefined()
  })

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
    await pickVanilla(noPackages)

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
      ...noPackages,
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
    "package.list": () => ({ packages: [] }),
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

  it("creates the instance from the curated package that was prepared", async () => {
    const caller = hub({ "package.list": () => ({ packages: [coder] }) })
    const clock = fakeClock()
    const user = userEvent.setup()
    render(<CreateWizard caller={caller} clock={clock} />)

    await user.click(await screen.findByRole("button", { name: "Prepare Software factory" }))
    await act(() => clock.advance(0))
    await user.click(await screen.findByRole("button", { name: "Continue" }))
    await nameIt(user)
    await fillSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toMatchObject({
      manifest_id: "Ada",
      package: coder.selection,
    })
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

  it("shows the fields a rebuilt image stored, and a retry sends the new one", async () => {
    let described = 0
    let creates = 0
    const rebuilt: PackageDescription = {
      ...vanilla,
      setup_fields: [
        ...vanilla.setup_fields,
        {
          name: "SEARCH_TOKEN",
          label: "Search token",
          description: "Reaches the search service.",
          kind: "secret",
          type: "text",
          required: true,
        },
      ],
    }
    const validateFailed = operationAnswer({
      operation_id: "op-create",
      kind: "create",
      state: "failed",
      detail: "The image asks for other setup values now. SEARCH_TOKEN: Search token is required.",
      steps: [
        { name: "image", state: "succeeded", detail: "Preparing the selected image." },
        {
          name: "validate",
          state: "failed",
          detail: "Checking the setup values against what the image declares.",
        },
      ],
    })
    const caller = hub({
      "package.describe": () => {
        described += 1
        return described === 1 ? vanilla : rebuilt
      },
      "instance.create": () => {
        creates += 1
        if (creates === 1) return { operation_id: "op-create", instance_id: "instance-1" }
        if (creates === 2) {
          throw new CallError({
            code: "INVALID_SETUP",
            message: "Some setup values are missing or invalid.",
            retryable: false,
            fields: { SEARCH_TOKEN: "Search token is required." },
          })
        }
        return { operation_id: "op-create-2", instance_id: "instance-2" }
      },
      "operation.get": ({ operation_id }) => (operation_id === "op-1" ? prepared : validateFailed),
    })
    const { user } = await openWizard(caller)
    await nameIt(user)
    await fillSetup(user)

    await user.click(screen.getByRole("button", { name: "Create instance" }))
    await user.click(await screen.findByRole("button", { name: "Back to setup" }))

    expect((await screen.findByLabelText("Search token")).getAttribute("type")).toBe("password")
    await user.click(screen.getByRole("button", { name: "Back" }))
    await user.click(screen.getByRole("button", { name: "Back" }))
    const asked = within(screen.getByRole("list", { name: "Setup fields" }))
    expect(asked.getByRole("listitem", { name: "Search token" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Prepare vanilla" }).hasAttribute("disabled")).toBe(
      true,
    )
    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Continue" }))

    await user.click(screen.getByRole("button", { name: "Create instance" }))

    const refused = await screen.findByLabelText("Search token")
    const secrets = within(screen.getByRole("group", { name: "Secrets" }))
    expect(refused.getAttribute("aria-invalid")).toBe("true")
    expect(secrets.getByRole("alert").textContent).toBe("Search token is required.")

    await user.type(refused, "search-token")
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    const sent = caller.calls.filter((call) => call.method === "instance.create")
    expect(sent).toHaveLength(3)
    expect(sent[2]?.params).toMatchObject({
      secrets: { api_key: "sk-private", SEARCH_TOKEN: "search-token" },
    })
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

describe("the create wizard's sign in step", () => {
  const codex: SubscriptionLogin = {
    id: "codex",
    label: "Codex",
    description: "Signs Codex in with your ChatGPT plan.",
    command: ["codex", "login", "--device-auth"],
    volume: "/root/.codex",
    prompt_pattern: "(?P<url>https://\\S+)\\s+(?P<code>\\S+)",
  }
  const published = operationAnswer({
    operation_id: "op-create",
    instance_id: "instance-1",
    kind: "create",
    state: "succeeded",
    steps: [{ name: "publish", state: "succeeded", detail: "Instance prepared and stopped." }],
  })
  const container: OperationStep = {
    name: "container",
    state: "succeeded",
    detail: "Starting the setup container.",
  }
  const waiting = operationAnswer({
    operation_id: "op-login",
    instance_id: "instance-1",
    kind: "login",
    state: "running",
    steps: [
      container,
      {
        name: "sign-in",
        state: "running",
        detail: "Waiting for you to sign in to Codex.",
        prompt: { url: "https://auth.openai.com/codex/device", code: "ABCD-12345" },
      },
    ],
  })

  /** A hub whose prepared image declares Codex, and answers the login's polls in order. */
  function withLogin(...logins: OperationGetResult[]) {
    return hub({
      "package.describe": () => ({ ...vanilla, logins: [codex] }),
      "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
      "operation.get": ({ operation_id }) => {
        if (operation_id === "op-1") return prepared
        if (operation_id === "op-create") return published
        return (logins.length > 1 ? logins.shift() : logins[0]) as OperationGetResult
      },
    })
  }

  async function created(caller: StubCaller) {
    const { user, clock } = await openWizard(caller)
    await nameIt(user)
    await fillSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))
    await act(() => clock.advance(0))
    return { user, clock }
  }

  const row = () =>
    within(within(screen.getByRole("list", { name: "Subscription logins" })).getByRole("listitem"))

  it("lists each login the image declares, not signed in yet", async () => {
    await created(withLogin(waiting))

    expect(row().getByText("Codex")).toBeDefined()
    expect(row().getByText("Signs Codex in with your ChatGPT plan.")).toBeDefined()
    expect(row().getByText("Not signed in")).toBeDefined()
    expect(screen.getByRole("button", { name: "Start and chat" })).toBeDefined()
  })

  it("shows the URL and the code while the hub waits for the sign-in", async () => {
    const caller = withLogin(waiting)
    const { user, clock } = await created(caller)

    await user.click(row().getByRole("button", { name: "Sign in" }))
    await act(() => clock.advance(0))

    expect(caller.calls.find((call) => call.method === "instance.login.start")?.params).toEqual({
      instance_id: "instance-1",
      login_id: "codex",
    })
    const link = row().getByRole("link", { name: "https://auth.openai.com/codex/device" })
    expect(link.getAttribute("href")).toBe("https://auth.openai.com/codex/device")
    expect(link.getAttribute("target")).toBe("_blank")
    expect(row().getByText("ABCD-12345").tagName).toBe("CODE")
    expect(row().getByText("Waiting")).toBeDefined()
    expect(row().getByRole("button", { name: "Sign in" }).hasAttribute("disabled")).toBe(true)
  })

  it("turns the row green once the sign-in succeeded", async () => {
    const signedIn = operationAnswer({
      operation_id: "op-login",
      kind: "login",
      state: "succeeded",
      detail: "Signed in.",
    })
    const { user, clock } = await created(withLogin(waiting, signedIn))

    await user.click(row().getByRole("button", { name: "Sign in" }))
    await act(() => clock.advance(0))
    await act(() => clock.advance(1_000))

    const badge = row().getByText("Signed in")
    expect(badge.getAttribute("data-variant")).toBe("success")
    expect(row().queryByRole("link")).toBeNull()
  })

  it("says the code expired, and signs in again for a new one", async () => {
    const detail = "The code expired. Sign in again for a new one."
    const caller = withLogin(
      operationAnswer({ operation_id: "op-login", kind: "login", state: "failed", detail }),
      waiting,
    )
    const { user, clock } = await created(caller)

    await user.click(row().getByRole("button", { name: "Sign in" }))
    await act(() => clock.advance(0))

    expect(row().getByText(detail)).toBeDefined()
    expect(row().getByText("Failed")).toBeDefined()
    await user.click(row().getByRole("button", { name: "Sign in again" }))
    await act(() => clock.advance(0))

    expect(row().getByText("ABCD-12345")).toBeDefined()
    expect(caller.calls.filter((call) => call.method === "instance.login.start")).toHaveLength(2)
  })

  it("has no sign in step when the image declares no login", async () => {
    await created(hub({}, published))

    expect(await screen.findByRole("button", { name: "Start and chat" })).toBeDefined()
    expect(screen.queryByRole("list", { name: "Subscription logins" })).toBeNull()
  })
})
