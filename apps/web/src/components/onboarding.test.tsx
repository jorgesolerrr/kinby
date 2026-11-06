import { CallError } from "@kinby/contract"
import type {
  DeclaredSetup,
  InstanceCreateCommand,
  LibraryEntry,
  OperationGetResult,
  OperationStep,
  SubscriptionLogin,
} from "@kinby/contract"
import { type Answers, type StubCaller, fakeClock, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, within } from "@testing-library/react"
import userEvent, { type UserEvent } from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { Onboarding } from "@/components/onboarding"

const vanilla: DeclaredSetup = {
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
      default: "You are a personal AI teammate.\n",
    },
  ],
}

/** An image that declares a field of every other type, each with a default. */
const typed: DeclaredSetup = {
  ...vanilla,
  setup_fields: [
    ...vanilla.setup_fields,
    {
      name: "tone",
      label: "Tone",
      description: "How drafts sound.",
      kind: "config",
      type: "choice",
      required: true,
      default: "plain",
      choices: ["plain", "formal"],
    },
    {
      name: "review",
      label: "Review",
      description: "Review each draft before it opens.",
      kind: "config",
      type: "boolean",
      required: false,
      default: true,
    },
    {
      name: "steps",
      label: "Steps per turn",
      description: "How many steps one turn may take.",
      kind: "config",
      type: "integer",
      required: false,
      default: 7,
    },
    {
      name: "commit_email",
      label: "Commit email",
      description: "The address commits are signed with.",
      kind: "config",
      type: "email",
      required: false,
      default: "kinby@example.com",
    },
    {
      name: "repository",
      label: "Repository",
      description: "The repository to clone.",
      kind: "config",
      type: "url",
      required: false,
      default: "https://github.com/owner/name.git",
    },
  ],
}

const codex: SubscriptionLogin = {
  id: "codex",
  label: "Codex",
  description: "Signs Codex in with your ChatGPT plan.",
  command: ["codex", "login", "--device-auth"],
  volume: "/root/.codex",
  prompt_pattern: "(?P<url>https://\\S+)\\s+(?P<code>\\S+)",
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
    "image.describe": () => vanilla,
  }
}

async function prepareImage(answers: Answers) {
  const clock = fakeClock()
  const caller = stubCaller(answers)
  render(<Onboarding caller={caller} clock={clock} />)
  await act(() => clock.advance(0))
  return { caller, clock }
}

/** The page's header, which holds the avatar, the name, the image's state and Create. */
function header() {
  const banner = screen.getByRole("heading", { level: 1 }).closest("header")
  if (banner === null) throw new Error("The page has no header.")
  return within(banner)
}

/** What Create says still blocks it, or nothing once it can create. */
function blocking() {
  const create = screen.getByRole("button", { name: "Create" })
  const reason = create.getAttribute("aria-describedby")
  return {
    disabled: create.hasAttribute("disabled"),
    reason: reason === null ? undefined : document.getElementById(reason)?.textContent,
  }
}

describe("the onboarding's page", () => {
  it("prepares the image as it opens, with no click", async () => {
    const { caller } = await prepareImage(preparation({}))

    expect(caller.calls.filter((call) => call.method === "image.prepare")).toEqual([
      { method: "image.prepare", params: {} },
    ])
    expect(screen.queryByRole("button", { name: /Prepare/ })).toBeNull()
  })

  it("shows every section at once, in order, with no Continue or Back", async () => {
    await prepareImage(preparation({ state: "succeeded" }))

    const titles = screen
      .getAllByRole("region")
      .map(
        (region) =>
          document.getElementById(region.getAttribute("aria-labelledby") ?? "")?.textContent,
      )
    expect(titles).toEqual(["Role", "Name and look", "Skills", "Prompt", "Setup"])
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull()
  })

  it("heads the page with the avatar and the name as typed", async () => {
    await prepareImage(preparation({ state: "succeeded" }))

    expect(header().getByRole("heading", { name: "New instance" })).toBeDefined()
    await userEvent.setup().type(screen.getByLabelText("Name"), "Ada")

    expect(header().getByRole("heading", { name: "Ada" })).toBeDefined()
    expect(header().getByRole("img", { name: "Avatar of Ada" })).toBeDefined()
  })

  it("asks for the setup fields only once the image is prepared", async () => {
    await prepareImage(preparation({}))

    expect(screen.getByText("The setup fields show once the image is prepared.")).toBeDefined()
    expect(screen.queryByLabelText("Model")).toBeNull()
  })
})

describe("the onboarding's image", () => {
  it("shows the step the preparation is running, then that the image is ready", async () => {
    let state: "running" | "succeeded" = "running"
    const { clock } = await prepareImage({
      ...preparation({}),
      "operation.get": () =>
        operationAnswer({
          state,
          steps: [
            {
              name: "image",
              state,
              detail: "Building the image, or reusing the one prepared.",
            },
          ],
        }),
    })

    expect(
      header().getByText("Preparing the image · Building the image, or reusing the one prepared."),
    ).toBeDefined()
    state = "succeeded"
    await act(() => clock.advance(5_000))

    expect(header().getByText("Image ready")).toBeDefined()
  })

  it("says which step failed and why, with Prepare again", async () => {
    await prepareImage(
      preparation({
        state: "failed",
        detail: 'Executable "claude" is not on PATH.',
        steps: [
          { name: "image", state: "succeeded", detail: "Building the image." },
          { name: "describe", state: "failed", detail: 'Executable "claude" is not on PATH.' },
        ],
      }),
    )

    expect(header().getByRole("alert").textContent).toBe(
      'The preparation failed at describe: Executable "claude" is not on PATH.',
    )
    expect(screen.queryByLabelText("Model")).toBeNull()
    expect(header().getByRole("button", { name: "Prepare again" }).hasAttribute("disabled")).toBe(
      false,
    )
  })

  it("says why when the preparation could not start", async () => {
    await prepareImage({})

    expect(header().getByRole("alert").textContent).toContain(
      "The stub has no answer for image.prepare.",
    )
    expect(header().getByRole("button", { name: "Prepare again" })).toBeDefined()
  })

  it("prepares the image again after the preparation failed", async () => {
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
    render(<Onboarding caller={caller} clock={clock} />)
    await act(() => clock.advance(0))

    await userEvent.setup().click(header().getByRole("button", { name: "Prepare again" }))
    await act(() => clock.advance(0))

    expect(
      header().getByText("Preparing the image · Building the image, or reusing the one prepared."),
    ).toBeDefined()
    expect(header().queryByRole("button", { name: "Prepare again" })).toBeNull()
    expect(caller.calls.filter((call) => call.method === "image.prepare")).toEqual([
      { method: "image.prepare", params: {} },
      { method: "image.prepare", params: {} },
    ])
  })

  it("lists each login the prepared image declares, after the setup fields", async () => {
    await prepareImage({
      ...preparation({ state: "succeeded" }),
      "image.describe": () => ({ ...vanilla, logins: [codex] }),
    })

    const setup = within(await screen.findByRole("list", { name: "Setup" }))
    const login = setup.getByRole("listitem", { name: "Codex" })
    expect(within(login).getByText("Signs Codex in with your ChatGPT plan.")).toBeDefined()
    expect(within(login).getByText("Sign in after it's created")).toBeDefined()
    expect(setup.getAllByRole("listitem").at(-1)).toBe(login)
  })

  // jsdom lays nothing out, so the line clamp shows only in the classes.
  it("shows each login's whole description, however narrow the screen", async () => {
    await prepareImage({
      ...preparation({ state: "succeeded" }),
      "image.describe": () => ({ ...vanilla, logins: [codex] }),
    })

    const login = within(await screen.findByRole("listitem", { name: "Codex" }))
    const description = login.getByText("Signs Codex in with your ChatGPT plan.")
    expect(description.className).not.toMatch(/line-clamp-\d/)
  })

  it("lists no login when the image declares none", async () => {
    await prepareImage(preparation({ state: "succeeded" }))

    expect(screen.queryByRole("listitem", { name: "Codex" })).toBeNull()
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

/**
 * A hub that prepared the image, with no factory and an empty library, and answers the create
 * operation's polls with `created`.
 */
function hub(answers: Answers, created: OperationGetResult = creating) {
  return stubCaller({
    "image.prepare": () => ({ operation_id: "op-1" }),
    "image.describe": () => vanilla,
    "factory.list": () => ({ factories: [] }),
    "library.list": () => ({ entries: [] }),
    "instance.create": () => ({ operation_id: "op-create", instance_id: "instance-1" }),
    "operation.get": ({ operation_id }) => (operation_id === "op-1" ? prepared : created),
    "hub.secret.list": () => ({ secrets: [] }),
    ...answers,
  })
}

/** Open the onboarding once the image is prepared. */
async function openOnboarding(caller: StubCaller, onPublished = () => {}) {
  const clock = fakeClock()
  const user = userEvent.setup()
  render(<Onboarding caller={caller} clock={clock} onPublished={onPublished} />)
  await act(() => clock.advance(0))
  return { user, clock }
}

const create = (user: UserEvent) => user.click(screen.getByRole("button", { name: "Create" }))

/** The prompt's row, folded or not. */
const promptRow = () => screen.getByRole("button", { name: /^Behavior prompt/ })

/** Unfold the prompt's row and return its editor. */
async function openPrompt(user: UserEvent) {
  await user.click(promptRow())
  return screen.getByLabelText("Behavior prompt") as HTMLTextAreaElement
}

/** Pick no role, name the instance, keep the skills, and write a prompt. */
async function nameAndPrompt(user: UserEvent, name = "Ada") {
  await user.type(screen.getByLabelText("Name"), name)
  const prompt = await openPrompt(user)
  await user.clear(prompt)
  await user.type(prompt, "Answer in haiku.")
}

/** The setup row whose control has *label*. */
function setupRow(label: string | RegExp) {
  const row = screen.getByLabelText(label).closest("li")
  if (row === null) throw new Error(`No setup row holds ${label}.`)
  return within(row)
}

async function fillSetup(user: UserEvent) {
  await user.type(screen.getByLabelText("Model"), "openai:gpt-5")
  await user.type(screen.getByLabelText("API key"), "sk-private")
}

describe("the onboarding's name and look", () => {
  it("names the instance and draws the avatar picked for it", async () => {
    const { user } = await openOnboarding(hub({}))

    await user.type(screen.getByLabelText("Name"), "Ada")
    await user.click(screen.getByRole("button", { name: "Squircle" }))
    await user.click(screen.getByRole("button", { name: "Green" }))

    const preview = screen.getByRole("img", { name: "Avatar of Ada" })
    expect(preview.getAttribute("data-shape")).toBe("squircle")
    expect(preview.querySelector("[data-slot=avatar-fallback]")?.getAttribute("data-variant")).toBe(
      "green",
    )
  })

  it("starts from a circle in the palette's first color", async () => {
    const { user } = await openOnboarding(hub({}))

    await user.type(screen.getByLabelText("Name"), "Ada")

    const preview = screen.getByRole("img", { name: "Avatar of Ada" })
    expect(preview.getAttribute("data-shape")).toBe("circle")
    expect(preview.querySelector("[data-slot=avatar-fallback]")?.getAttribute("data-variant")).toBe(
      "blue",
    )
  })
})

describe("the onboarding's Create", () => {
  it("waits for the image, then the name, then each required configuration field", async () => {
    let state: "running" | "succeeded" = "running"
    const caller = hub({
      "operation.get": () => operationAnswer({ state }),
      "image.describe": () => typed,
    })
    const { user, clock } = await openOnboarding(caller)

    expect(blocking()).toEqual({ disabled: true, reason: "The image is preparing" })
    state = "succeeded"
    await act(() => clock.advance(5_000))
    expect(blocking()).toEqual({ disabled: true, reason: "Name it" })
    await user.type(screen.getByLabelText("Name"), "Ada")
    expect(blocking()).toEqual({ disabled: true, reason: "1 required configuration field left" })
    await user.type(screen.getByLabelText("Model"), "openai:gpt-5")

    expect(blocking()).toEqual({ disabled: false, reason: undefined })
  })

  it("creates with a required secret left empty and no hub secret picked", async () => {
    const { user } = await openOnboarding(hub({}))

    await user.type(screen.getByLabelText("Name"), "Ada")
    await user.type(screen.getByLabelText("Model"), "openai:gpt-5")

    expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("")
    expect(blocking()).toEqual({ disabled: false, reason: undefined })
  })

  it("says the image did not prepare while it failed", async () => {
    await openOnboarding(
      hub({
        "operation.get": () => operationAnswer({ state: "failed", detail: "No Docker." }),
      }),
    )

    expect(blocking()).toEqual({ disabled: true, reason: "The image did not prepare" })
  })
})

describe("the onboarding's setup", () => {
  it("asks for each declared field, and never shows a secret it was given", async () => {
    const { user } = await openOnboarding(hub({}))
    await nameAndPrompt(user)

    const setup = within(screen.getByRole("list", { name: "Setup" }))
    expect(setup.getByLabelText("Model").tagName).toBe("INPUT")
    expect(setup.queryByLabelText(/Behavior prompt/)).toBeNull()
    expect(setup.getByLabelText("API key").getAttribute("type")).toBe("password")
  })

  it("says every instance asks for the built-in fields, and the image for its own", async () => {
    await openOnboarding(hub({ "image.describe": () => typed }))

    expect(
      setupRow("Model").getByText("The model the instance calls. · every instance"),
    ).toBeDefined()
    expect(
      setupRow("API key").getByText("The key your model provider issued. · every instance"),
    ).toBeDefined()
    expect(setupRow("Tone").getByText("How drafts sound. · asked by the image")).toBeDefined()
  })

  it("counts the required configuration fields left, not the secrets", async () => {
    const { user } = await openOnboarding(hub({}))
    const setup = within(screen.getByRole("region", { name: "Setup" }))

    expect(setup.getByText("1 required configuration field left")).toBeDefined()
    await user.type(screen.getByLabelText("Model"), "openai:gpt-5")

    expect(setup.queryByText(/required configuration fields? left/)).toBeNull()
  })

  it("creates the instance from the values and the identity", async () => {
    const caller = hub({})
    const { user } = await openOnboarding(caller)
    await user.type(screen.getByLabelText("Name"), "Ada")
    await user.click(screen.getByRole("button", { name: "Square" }))
    const prompt = await openPrompt(user)
    await user.clear(prompt)
    await user.type(prompt, "Answer in haiku.")
    await fillSetup(user)

    await create(user)

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toEqual({
      manifest_id: "Ada",
      persona_name: "Ada",
      model: "openai:gpt-5",
      config: { behavior_prompt: "Answer in haiku." },
      secrets: { api_key: "sk-private" },
      hub_secrets: {},
      save_to_hub: {},
      avatar: { shape: "square", color: "blue" },
      picks: [],
    })
    const steps = within(await screen.findByRole("list", { name: "Creation steps" }))
    expect(steps.getByText("Preparing the selected image.")).toBeDefined()
  })

  it("does not send an untouched boolean or a blank optional secret", async () => {
    const untouched: DeclaredSetup = {
      ...vanilla,
      setup_fields: [
        ...vanilla.setup_fields,
        {
          name: "share",
          label: "Share",
          description: "Share drafts.",
          kind: "config",
          type: "boolean",
          required: false,
        },
        {
          name: "DRAFT_TOKEN",
          label: "Draft token",
          description: "Optional token.",
          kind: "secret",
          type: "text",
          required: false,
        },
      ],
    }
    const caller = hub({ "image.describe": () => untouched })
    const { user } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await fillSetup(user)

    expect(screen.getByRole("switch", { name: /Share/ }).getAttribute("aria-checked")).toBe("false")
    await create(user)

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toEqual({
      manifest_id: "Ada",
      persona_name: "Ada",
      model: "openai:gpt-5",
      config: { behavior_prompt: "Answer in haiku." },
      secrets: { api_key: "sk-private" },
      hub_secrets: {},
      save_to_hub: {},
      avatar: { shape: "circle", color: "blue" },
      picks: [],
    })
  })

  it("sends a boolean once the user sets the switch", async () => {
    const untouched: DeclaredSetup = {
      ...vanilla,
      setup_fields: [
        ...vanilla.setup_fields,
        {
          name: "share",
          label: "Share",
          description: "Share drafts.",
          kind: "config",
          type: "boolean",
          required: false,
        },
      ],
    }
    const caller = hub({ "image.describe": () => untouched })
    const { user } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await fillSetup(user)

    await user.click(screen.getByRole("switch", { name: /Share/ }))
    await create(user)

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toMatchObject({
      config: { share: true },
    })
  })

  it("renders each field by its type, starting from its default", async () => {
    const { user } = await openOnboarding(hub({ "image.describe": () => typed }))
    await nameAndPrompt(user)

    const configuration = within(screen.getByRole("list", { name: "Setup" }))
    expect(configuration.getByRole("combobox", { name: "Tone" }).textContent).toContain("plain")
    expect(configuration.getByRole("switch", { name: /Review/ }).getAttribute("aria-checked")).toBe(
      "true",
    )
    expect(configuration.getByRole("switch", { name: /^Review ?\(optional\)$/ })).toBeDefined()
    const steps = configuration.getByRole("spinbutton", { name: /Steps per turn/ })
    expect((steps as HTMLInputElement).value).toBe("7")
    const email = configuration.getByLabelText(/Commit email/) as HTMLInputElement
    expect(email.type).toBe("email")
    expect(email.value).toBe("kinby@example.com")
    const repository = configuration.getByLabelText(/Repository/) as HTMLInputElement
    expect(repository.type).toBe("url")
    expect(repository.value).toBe("https://github.com/owner/name.git")
  })

  it("sends a picked choice, a switch, and a whole number as typed values", async () => {
    const caller = hub({ "image.describe": () => typed })
    const { user } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await fillSetup(user)

    await user.click(screen.getByRole("combobox", { name: "Tone" }))
    await user.click(await screen.findByRole("option", { name: "formal" }))
    await user.click(screen.getByRole("switch", { name: /Review/ }))
    const steps = screen.getByRole("spinbutton", { name: /Steps per turn/ })
    await user.clear(steps)
    await user.type(steps, "12")
    await create(user)

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toMatchObject({
      config: { behavior_prompt: "Answer in haiku.", tone: "formal", review: false, steps: 12 },
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
    const { user } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await fillSetup(user)

    await create(user)

    const model = await screen.findByLabelText("Model")
    expect(model.getAttribute("aria-invalid")).toBe("true")
    expect(setupRow("Model").getByRole("alert").textContent).toBe(
      "Name the provider and the model, like openai:gpt-5.",
    )
    expect(screen.getByLabelText("API key").getAttribute("aria-invalid")).toBeNull()
    expect(screen.queryByRole("list", { name: "Creation steps" })).toBeNull()
  })

  it("sends a malformed email and URL and shows the hub's error under each", async () => {
    const caller = hub({
      "image.describe": () => typed,
      "instance.create": () => {
        throw new CallError({
          code: "INVALID_SETUP",
          message: "Some setup values are missing or invalid.",
          retryable: false,
          fields: {
            commit_email: "Commit email is not an email address, like someone@example.com.",
            repository:
              "Repository is not a URL with a scheme and a host, like https://example.com.",
          },
        })
      },
    })
    const { user } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await fillSetup(user)
    const email = screen.getByLabelText(/Commit email/)
    const repository = screen.getByLabelText(/Repository/)
    await user.clear(email)
    await user.type(email, "not-an-email")
    await user.clear(repository)
    await user.type(repository, "not a repository")

    await create(user)

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toMatchObject({
      config: { commit_email: "not-an-email", repository: "not a repository" },
    })
    const errors = within(await screen.findByRole("list", { name: "Setup" }))
      .getAllByRole("alert")
      .map((alert) => alert.textContent)
    expect(errors).toEqual([
      "Commit email is not an email address, like someone@example.com.",
      "Repository is not a URL with a scheme and a host, like https://example.com.",
    ])
    expect(screen.getByLabelText(/Commit email/).getAttribute("aria-invalid")).toBe("true")
    expect(screen.getByLabelText(/Repository/).getAttribute("aria-invalid")).toBe("true")
  })

  it("asks for the fields a rebuilt image stored, and a retry sends the new one", async () => {
    let described = 0
    let creates = 0
    const rebuilt: DeclaredSetup = {
      ...vanilla,
      setup_fields: [
        ...vanilla.setup_fields,
        {
          name: "search_region",
          label: "Search region",
          description: "Where the search service runs.",
          kind: "config",
          type: "text",
          required: true,
        },
      ],
    }
    const validateFailed = operationAnswer({
      operation_id: "op-create",
      kind: "create",
      state: "failed",
      detail:
        "The image asks for other setup values now. search_region: Search region is required.",
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
      "image.describe": () => {
        described += 1
        return described === 1 ? vanilla : rebuilt
      },
      "instance.create": () => {
        creates += 1
        return creates === 1
          ? { operation_id: "op-create", instance_id: "instance-1" }
          : { operation_id: "op-create-2", instance_id: "instance-2" }
      },
      "operation.get": ({ operation_id }) => (operation_id === "op-1" ? prepared : validateFailed),
    })
    const { user } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await fillSetup(user)

    await create(user)
    await user.click(await screen.findByRole("button", { name: "Back to the form" }))

    const region = await screen.findByLabelText("Search region")
    expect(blocking()).toEqual({ disabled: true, reason: "1 required configuration field left" })
    expect(screen.queryByRole("button", { name: /Prepare/ })).toBeNull()
    await user.type(region, "eu")
    await create(user)

    const sent = caller.calls.filter((call) => call.method === "instance.create")
    expect(sent).toHaveLength(2)
    expect(sent[1]?.params).toMatchObject({
      config: { search_region: "eu" },
    })
  })
})

/** The `coder` role's suggested picks in the shipped `software` factory. */
const SUGGESTED = [
  "adversarial-review",
  "api-and-interface-design",
  "codebase-design",
  "no-comments",
  "ponytail",
  "pr",
  "tdd",
  "unslop",
]

const [modelField, apiKeyField] = vanilla.setup_fields

/** What the coder asks for: the built-in fields, a repository and a GitHub token, its prompt. */
const coder: DeclaredSetup = {
  setup_fields: [
    ...(modelField && apiKeyField ? [modelField, apiKeyField] : []),
    {
      name: "repository",
      label: "Repository",
      description: "The repository the coder works in.",
      kind: "config",
      type: "url",
      required: true,
    },
    {
      name: "GH_TOKEN",
      label: "GitHub token",
      description: "Opens pull requests.",
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
      default: "You implement tickets.\n",
    },
  ],
}

function skill(name: string): LibraryEntry {
  return { kind: "skill", name, description: `The ${name} skill.`, source: "shipped" }
}

/** A hub whose library holds the `software` factory, its suggested picks and one more skill. */
function softwareHub(answers: Answers = {}) {
  return hub({
    "factory.list": () => ({ factories: [{ name: "software", source: "shipped" }] }),
    "factory.describe": () => ({ instances: { coder }, suggested_picks: { coder: SUGGESTED } }),
    "library.list": () => ({
      entries: [
        { kind: "factory", name: "software", description: null, source: "shipped" },
        ...[...SUGGESTED, "house-style"].map(skill),
      ],
    }),
    ...answers,
  })
}

const roleRow = (name: RegExp) =>
  within(screen.getByRole("list", { name: "Roles" })).findByRole("button", { name })

async function pickCoder(user: UserEvent) {
  await user.click(await roleRow(/^coder in software/))
}

/** The skills' rows in order, by name. */
const skillRows = () =>
  within(screen.getByRole("list", { name: "Skills" }))
    .getAllByRole("listitem")
    .map((row) => row.getAttribute("aria-label"))

const skillRow = (name: string) =>
  within(within(screen.getByRole("list", { name: "Skills" })).getByRole("listitem", { name }))

async function fillCoderSetup(user: UserEvent) {
  await fill(user, "Model", "anthropic:claude-sonnet-5")
  await fill(user, "API key", "sk-ant")
  await fill(user, "Repository", "https://github.com/owner/billing.git")
  await fill(user, "GitHub token", "ghp-test")
}

/** Paste a value into a field: typing it would re-render the page on every key. */
async function fill(user: UserEvent, label: string, value: string) {
  await user.click(screen.getByLabelText(label))
  await user.paste(value)
}

const sent = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.create").map((call) => call.params)

const ticked = (name: string) =>
  screen.getByRole("checkbox", { name }).getAttribute("aria-checked") === "true"

describe("the onboarding's secret fields and hub secrets", () => {
  const withToken: DeclaredSetup = {
    ...vanilla,
    setup_fields: [
      ...vanilla.setup_fields,
      {
        name: "GITHUB_TOKEN",
        label: "GitHub token",
        description: "A token that can push.",
        kind: "secret",
        type: "text",
        required: true,
      },
    ],
  }

  const keeping = (...names: string[]) =>
    hub({
      "image.describe": () => withToken,
      "hub.secret.list": () => ({ secrets: names.map((name) => ({ name })) }),
    })

  /** What the create command carries for the secret fields: typed, linked and saved. */
  const secretsSent = (caller: StubCaller) => {
    const [{ secrets, hub_secrets, save_to_hub }] = sent(caller) as InstanceCreateCommand[]
    return { secrets, hub_secrets, save_to_hub }
  }

  async function toSecrets(caller: StubCaller, model: string) {
    const { user } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await user.type(screen.getByLabelText("Model"), model)
    return user
  }

  it("preselects the hub secret named after each field's variable, the API key's provider's", async () => {
    const caller = keeping("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY")
    const user = await toSecrets(caller, "anthropic:claude-opus-5-5")

    expect(screen.getByRole("combobox", { name: "Hub secret for API key" }).textContent).toContain(
      "ANTHROPIC_API_KEY",
    )
    await create(user)

    expect(secretsSent(caller)).toEqual({
      secrets: {},
      hub_secrets: { api_key: "ANTHROPIC_API_KEY", GITHUB_TOKEN: "GITHUB_TOKEN" },
      save_to_hub: {},
    })
  })

  it("asks for the web search key the image describes, starting on its hub secret", async () => {
    const searching: DeclaredSetup = {
      ...vanilla,
      setup_fields: [
        ...vanilla.setup_fields,
        {
          name: "BRAVE_API_KEY",
          label: "Web search key",
          description: "The key of the search backend the instance's web search queries.",
          kind: "secret",
          type: "text",
          required: true,
        },
      ],
    }
    const caller = hub({
      "image.describe": () => searching,
      "hub.secret.list": () => ({ secrets: [{ name: "BRAVE_API_KEY" }] }),
    })
    const user = await toSecrets(caller, "openai:gpt-5")

    const row = setupRow(/Web search key/)
    expect(row.getByText(/asked by the image/)).toBeDefined()
    expect(row.queryByText("(optional)")).toBeNull()
    expect(
      screen.getByRole("combobox", { name: "Hub secret for Web search key" }).textContent,
    ).toContain("BRAVE_API_KEY")
    await create(user)

    expect(secretsSent(caller)).toEqual({
      secrets: {},
      hub_secrets: { BRAVE_API_KEY: "BRAVE_API_KEY" },
      save_to_hub: {},
    })
  })

  it("takes another hub secret for one field, and a typed value for another", async () => {
    const caller = keeping("OPENAI_API_KEY", "GITHUB_TOKEN", "WORK_TOKEN")
    const user = await toSecrets(caller, "openai:gpt-5")

    await user.click(screen.getByRole("combobox", { name: "Hub secret for GitHub token" }))
    await user.click(await screen.findByRole("option", { name: "WORK_TOKEN" }))
    await user.click(screen.getByRole("combobox", { name: "Hub secret for API key" }))
    await user.click(await screen.findByRole("option", { name: "Type a value" }))
    await user.type(screen.getByLabelText("API key"), "sk-private")
    await create(user)

    expect(secretsSent(caller)).toEqual({
      secrets: { api_key: "sk-private" },
      hub_secrets: { GITHUB_TOKEN: "WORK_TOKEN" },
      save_to_hub: {},
    })
  })

  it("types a value where no hub secret has the variable's name", async () => {
    const caller = keeping("WORK_TOKEN")
    const user = await toSecrets(caller, "openai:gpt-5")

    await user.type(screen.getByLabelText("API key"), "sk-private")
    await user.type(screen.getByLabelText("GitHub token"), "ghp-typed")
    await create(user)

    expect(secretsSent(caller)).toEqual({
      secrets: { api_key: "sk-private", GITHUB_TOKEN: "ghp-typed" },
      hub_secrets: {},
      save_to_hub: {},
    })
  })

  it("saves a typed value to the hub under the variable's name, or the one given", async () => {
    const caller = keeping()
    const user = await toSecrets(caller, "openai:gpt-5")

    expect(screen.queryByRole("combobox", { name: "Hub secret for API key" })).toBeNull()
    await user.type(screen.getByLabelText("API key"), "sk-private")
    await user.click(setupRow("API key").getByRole("checkbox", { name: "Save to the hub" }))
    const keyName = setupRow("API key").getByLabelText("Name on the hub") as HTMLInputElement
    expect(keyName.value).toBe("OPENAI_API_KEY")
    await user.type(screen.getByLabelText("GitHub token"), "ghp-typed")
    await user.click(setupRow("GitHub token").getByRole("checkbox", { name: "Save to the hub" }))
    const tokenName = setupRow("GitHub token").getByLabelText("Name on the hub")
    await user.clear(tokenName)
    await user.type(tokenName, "PERSONAL_TOKEN")
    await create(user)

    expect(secretsSent(caller)).toEqual({
      secrets: { api_key: "sk-private", GITHUB_TOKEN: "ghp-typed" },
      hub_secrets: {},
      save_to_hub: { api_key: "OPENAI_API_KEY", GITHUB_TOKEN: "PERSONAL_TOKEN" },
    })
  })

  it("saves nothing for an optional field left empty", async () => {
    const caller = hub({
      "image.describe": () => ({
        ...vanilla,
        setup_fields: withToken.setup_fields.map((field) =>
          field.name === "GITHUB_TOKEN" ? { ...field, required: false } : field,
        ),
      }),
    })
    const user = await toSecrets(caller, "openai:gpt-5")

    await user.type(screen.getByLabelText("API key"), "sk-private")
    await user.click(setupRow(/^GitHub token/).getByRole("checkbox", { name: "Save to the hub" }))
    await create(user)

    expect(secretsSent(caller)).toEqual({
      secrets: { api_key: "sk-private" },
      hub_secrets: {},
      save_to_hub: {},
    })
  })

  it("asks for a name before it saves a typed value to the hub", async () => {
    const caller = keeping()
    const user = await toSecrets(caller, "openai:gpt-5")

    await user.type(screen.getByLabelText("API key"), "sk-private")
    await user.click(setupRow("API key").getByRole("checkbox", { name: "Save to the hub" }))
    await user.clear(setupRow("API key").getByLabelText("Name on the hub"))

    expect(setupRow("API key").getByRole("alert").textContent).toBe(
      "Name the hub secret to save it under.",
    )
    const create = screen.getByRole("button", { name: "Create" })
    expect(create.hasAttribute("disabled")).toBe(true)
    await user.click(create)
    expect(sent(caller)).toEqual([])
  })

  it("keeps a value typed before the hub secrets arrive", async () => {
    let arrived = false
    const caller = hub({
      "image.describe": () => withToken,
      "hub.secret.list": () => {
        if (!arrived) {
          throw new CallError({ code: "CONNECTION_LOST", message: "dropped", retryable: false })
        }
        return { secrets: [{ name: "OPENAI_API_KEY" }] }
      },
    })
    const { user, clock } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await user.type(screen.getByLabelText("Model"), "openai:gpt-5")

    await user.type(screen.getByLabelText("API key"), "sk-private")
    await user.type(screen.getByLabelText("GitHub token"), "ghp-typed")
    arrived = true
    await act(() => clock.advance(5_000))

    expect(screen.getByRole("combobox", { name: "Hub secret for API key" }).textContent).toContain(
      "Type a value",
    )
    await create(user)
    expect(secretsSent(caller)).toEqual({
      secrets: { api_key: "sk-private", GITHUB_TOKEN: "ghp-typed" },
      hub_secrets: {},
      save_to_hub: {},
    })
  })
})

describe("the onboarding's role, skills and prompt", () => {
  it("creates a vanilla instance with no role, the skills ticked and the prompt written", async () => {
    const caller = hub({ "library.list": () => ({ entries: [skill("pr"), skill("tdd")] }) })
    const { user } = await openOnboarding(caller)
    await user.type(screen.getByLabelText("Name"), "Ada")

    expect([ticked("pr"), ticked("tdd")]).toEqual([false, false])
    await user.click(await screen.findByRole("checkbox", { name: "tdd" }))
    const prompt = await openPrompt(user)
    expect(prompt.value).toBe("You are a personal AI teammate.\n")
    await user.clear(prompt)
    await user.type(prompt, "Answer in haiku.")
    await fillSetup(user)
    await create(user)

    expect(sent(caller)).toEqual([
      {
        manifest_id: "Ada",
        persona_name: "Ada",
        model: "openai:gpt-5",
        config: { behavior_prompt: "Answer in haiku." },
        secrets: { api_key: "sk-private" },
        hub_secrets: {},
        save_to_hub: {},
        avatar: { shape: "circle", color: "blue" },
        picks: ["tdd"],
      },
    ])
  })

  it("lists the roles as rows, None first, each with its factory and the skills it suggests", async () => {
    const { user } = await openOnboarding(softwareHub())

    const coderRow = await roleRow(/^coder in software/)
    const noneRow = await roleRow(/^None/)
    const roles = within(screen.getByRole("list", { name: "Roles" }))
    expect(roles.getAllByRole("button")).toEqual([noneRow, coderRow])
    expect(
      within(noneRow).getByText("A vanilla instance, from kinby's built-in defaults."),
    ).toBeDefined()
    expect(within(coderRow).getByText("Suggests 8 skills.")).toBeDefined()
    expect([noneRow, coderRow].map((row) => row.getAttribute("aria-pressed"))).toEqual([
      "true",
      "false",
    ])
    await user.click(coderRow)

    expect([noneRow, coderRow].map((row) => row.getAttribute("aria-pressed"))).toEqual([
      "false",
      "true",
    ])
  })

  it("puts the role's suggested picks first, ticked and badged, and counts the picked", async () => {
    const { user } = await openOnboarding(
      softwareHub({
        "library.list": () => ({ entries: ["house-style", ...SUGGESTED].map(skill) }),
      }),
    )
    await screen.findByRole("checkbox", { name: "tdd" })
    const skills = within(screen.getByRole("region", { name: "Skills" }))

    expect(skillRows()).toEqual(["house-style", ...SUGGESTED])
    expect(skills.getByText("0 picked")).toBeDefined()
    await pickCoder(user)

    expect(skillRows()).toEqual([...SUGGESTED, "house-style"])
    expect(SUGGESTED.filter(ticked)).toEqual(SUGGESTED)
    expect(skillRow("tdd").getByText("Suggested by coder")).toBeDefined()
    expect(skillRow("house-style").queryByText(/Suggested by/)).toBeNull()
    expect(skills.getByText("8 picked")).toBeDefined()
    await user.click(screen.getByRole("checkbox", { name: "house-style" }))
    expect(skills.getByText("9 picked")).toBeDefined()
  })

  it("folds the prompt to its first line and whose default it is", async () => {
    const { user } = await openOnboarding(softwareHub())

    expect(within(promptRow()).getByText("kinby's default")).toBeDefined()
    expect(within(promptRow()).getByText("You are a personal AI teammate.")).toBeDefined()
    expect(screen.queryByLabelText("Behavior prompt")).toBeNull()
    await pickCoder(user)

    expect(within(promptRow()).getByText("coder's default")).toBeDefined()
    expect(within(promptRow()).getByText("You implement tickets.")).toBeDefined()
  })

  it("marks an edited prompt, and Back to the default restores it", async () => {
    const caller = softwareHub()
    const { user } = await openOnboarding(caller)
    await pickCoder(user)

    const prompt = await openPrompt(user)
    expect(screen.queryByRole("button", { name: "Back to the default" })).toBeNull()
    await user.clear(prompt)
    await user.type(prompt, "Answer in haiku.")
    expect(within(promptRow()).getByText("Edited")).toBeDefined()
    expect(within(promptRow()).getByText("Answer in haiku.")).toBeDefined()
    await user.click(screen.getByRole("button", { name: "Back to the default" }))

    expect(prompt.value).toBe("You implement tickets.\n")
    expect(within(promptRow()).queryByText("Edited")).toBeNull()
    expect(within(promptRow()).getByText("coder's default")).toBeDefined()
    await fillCoderSetup(user)
    await create(user)
    expect(sent(caller)[0]).toMatchObject({
      config: { behavior_prompt: "You implement tickets.\n" },
    })
  })

  it("preselects the coder's suggested picks and asks for its setup fields", async () => {
    const { user } = await openOnboarding(softwareHub())
    await pickCoder(user)

    await screen.findByRole("checkbox", { name: "tdd" })
    expect(SUGGESTED.filter(ticked)).toEqual(SUGGESTED)
    expect(ticked("house-style")).toBe(false)
    expect(screen.getByLabelText("Repository")).toBeDefined()
    expect(screen.getByLabelText("GitHub token").getAttribute("type")).toBe("password")
  })

  it("names the role as who asks for its own fields, and every instance for the built-in ones", async () => {
    const { user } = await openOnboarding(softwareHub())
    await pickCoder(user)

    expect(
      setupRow("Repository").getByText("The repository the coder works in. · asked by coder"),
    ).toBeDefined()
    expect(
      setupRow("GitHub token").getByText("Opens pull requests. · asked by coder"),
    ).toBeDefined()
    expect(
      setupRow("Model").getByText("The model the instance calls. · every instance"),
    ).toBeDefined()
  })

  it("creates a coder without a suggested pick the user unticked", async () => {
    const caller = softwareHub()
    const { user } = await openOnboarding(caller)
    await pickCoder(user)

    await user.click(await screen.findByRole("checkbox", { name: "tdd" }))
    await fillCoderSetup(user)
    await create(user)

    expect(sent(caller)).toEqual([
      {
        manifest_id: "software-coder",
        persona_name: "software-coder",
        factory_role: { factory: "software", name: "coder" },
        model: "anthropic:claude-sonnet-5",
        config: {
          repository: "https://github.com/owner/billing.git",
          behavior_prompt: "You implement tickets.\n",
        },
        secrets: { api_key: "sk-ant", GH_TOKEN: "ghp-test" },
        hub_secrets: {},
        save_to_hub: {},
        avatar: { shape: "circle", color: "blue" },
        picks: SUGGESTED.filter((name) => name !== "tdd"),
      },
    ])
  })

  it("starts the prompt from the role's, and creates the coder with the edited text", async () => {
    const caller = softwareHub()
    const { user } = await openOnboarding(caller)
    await pickCoder(user)

    const prompt = await openPrompt(user)
    expect(prompt.value).toBe("You implement tickets.\n")
    await user.type(prompt, "Keep each pull request small.")
    await fillCoderSetup(user)
    await create(user)

    expect(sent(caller)[0]).toMatchObject({
      config: { behavior_prompt: "You implement tickets.\nKeep each pull request small." },
    })
  })

  it("proposes the factory and the role as the name, and takes another", async () => {
    const caller = softwareHub()
    const { user } = await openOnboarding(caller)
    await pickCoder(user)

    const name = screen.getByLabelText("Name") as HTMLInputElement
    expect(name.value).toBe("software-coder")
    await user.clear(name)
    await user.type(name, "billing-coder")
    await fillCoderSetup(user)
    await create(user)

    expect(sent(caller)[0]).toMatchObject({
      manifest_id: "billing-coder",
      persona_name: "billing-coder",
    })
  })

  it("keeps a name the user typed when a role is picked", async () => {
    const { user } = await openOnboarding(softwareHub())

    await user.type(screen.getByLabelText("Name"), "Ada")
    await pickCoder(user)

    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Ada")
  })

  it("asks for the fields of a role edited since it was picked, once the hub refuses", async () => {
    const prefix = {
      name: "branch_prefix",
      label: "Branch prefix",
      description: "Starts the name of each branch.",
      kind: "config" as const,
      type: "text" as const,
      required: true,
    }
    let describes = 0
    let creates = 0
    const caller = softwareHub({
      "factory.describe": () => {
        describes += 1
        const setup = describes === 1 ? coder : { setup_fields: [...coder.setup_fields, prefix] }
        return { instances: { coder: setup }, suggested_picks: { coder: SUGGESTED } }
      },
      "instance.create": () => {
        creates += 1
        if (creates === 1) {
          throw new CallError({
            code: "INVALID_SETUP",
            message: "Some setup values are missing or invalid.",
            retryable: false,
            fields: { branch_prefix: "Branch prefix is required." },
          })
        }
        return { operation_id: "op-create", instance_id: "instance-2" }
      },
    })
    const { user } = await openOnboarding(caller)
    await pickCoder(user)
    await fillCoderSetup(user)
    await create(user)

    const refused = await screen.findByLabelText("Branch prefix")
    expect(refused.getAttribute("aria-invalid")).toBe("true")
    await user.type(refused, "agent/")
    await create(user)

    expect(sent(caller)[1]).toMatchObject({
      config: { branch_prefix: "agent/" },
    })
  })

  it("creates a second coder on software once it has a name of its own", async () => {
    let creates = 0
    const caller = softwareHub({
      "instance.create": () => {
        creates += 1
        if (creates === 1) {
          throw new CallError({
            code: "INVALID_SETUP",
            message: "Some setup values are missing or invalid.",
            retryable: false,
            fields: { manifest_id: 'An instance named "software-coder" exists already.' },
          })
        }
        return { operation_id: "op-create", instance_id: "instance-2" }
      },
    })
    const { user } = await openOnboarding(caller)
    await pickCoder(user)
    await fillCoderSetup(user)
    await create(user)

    expect(
      await screen.findByText('An instance named "software-coder" exists already.'),
    ).toBeDefined()
    await user.clear(screen.getByLabelText("Name"))
    await user.type(screen.getByLabelText("Name"), "billing-coder")
    await create(user)

    expect(sent(caller)).toMatchObject([
      { manifest_id: "software-coder" },
      { manifest_id: "billing-coder" },
    ])
    expect(await screen.findByRole("list", { name: "Creation steps" })).toBeDefined()
  })
})

describe("the onboarding's start", () => {
  const published = operationAnswer({
    operation_id: "op-create",
    instance_id: "instance-1",
    kind: "create",
    state: "succeeded",
    steps: [{ name: "publish", state: "succeeded", detail: "Instance prepared and stopped." }],
  })

  beforeEach(() => window.history.replaceState(null, "", "/new"))

  async function createdAda(caller: StubCaller, onPublished = () => {}) {
    const { user, clock } = await openOnboarding(caller, onPublished)
    await nameAndPrompt(user)
    await fillSetup(user)
    await create(user)
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

    expect(await screen.findByRole("button", { name: "Back to the form" })).toBeDefined()
    expect(screen.queryByRole("button", { name: "Start and chat" })).toBeNull()
    expect(onPublished).not.toHaveBeenCalled()
  })

  it("opens the instance only after the list that follows a start", async () => {
    const release: Array<() => void> = []
    const onPublished = () =>
      new Promise<void>((resolve) => {
        release.push(resolve)
      })
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
    const { user, clock } = await createdAda(caller, onPublished)

    await user.click(await screen.findByRole("button", { name: "Start and chat" }))
    await act(() => clock.advance(0))

    expect(window.location.pathname).toBe("/new")
    expect(release).toHaveLength(2)
    await act(() => {
      release[1]?.()
      return Promise.resolve()
    })
    expect(window.location.pathname).toBe("/instances/instance-1")
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

describe("the onboarding's sign-ins", () => {
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
      "image.describe": () => ({ ...vanilla, logins: [codex] }),
      "instance.login.start": () => ({ operation_id: "op-login", instance_id: "instance-1" }),
      "operation.get": ({ operation_id }) => {
        if (operation_id === "op-1") return prepared
        if (operation_id === "op-create") return published
        return (logins.length > 1 ? logins.shift() : logins[0]) as OperationGetResult
      },
    })
  }

  async function created(caller: StubCaller) {
    const { user, clock } = await openOnboarding(caller)
    await nameAndPrompt(user)
    await fillSetup(user)
    await create(user)
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

  it("keeps the row signed in when signing in again after a sign-in expires", async () => {
    const detail = "The code expired. Sign in again for a new one."
    const caller = withLogin(
      operationAnswer({ operation_id: "op-login", kind: "login", state: "succeeded" }),
      operationAnswer({ operation_id: "op-login", kind: "login", state: "failed", detail }),
    )
    const { user, clock } = await created(caller)

    await user.click(row().getByRole("button", { name: "Sign in" }))
    await act(() => clock.advance(0))
    await user.click(row().getByRole("button", { name: "Sign in again" }))
    await act(() => clock.advance(0))

    expect(row().getByText("Signed in").getAttribute("data-variant")).toBe("success")
    expect(row().queryByText("Failed")).toBeNull()
    expect(
      row().getByText(
        `The new sign-in did not finish, and the one before it still works. ${detail}`,
      ),
    ).toBeDefined()
  })

  it("has no sign in step when the image declares no login", async () => {
    await created(hub({}, published))

    expect(await screen.findByRole("button", { name: "Start and chat" })).toBeDefined()
    expect(screen.queryByRole("list", { name: "Subscription logins" })).toBeNull()
  })
})
