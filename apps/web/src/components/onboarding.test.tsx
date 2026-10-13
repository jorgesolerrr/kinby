import { CallError } from "@kinby/contract"
import type {
  DeclaredSetup,
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
  render(<Onboarding caller={stubCaller(answers)} clock={clock} />)
  await userEvent.setup().click(screen.getByRole("button", { name: "Prepare the image" }))
  await act(() => clock.advance(0))
}

const step = (name: string) => within(screen.getByRole("listitem", { name }))

describe("the onboarding's image step", () => {
  it("prepares the image and shows the step running", async () => {
    await prepareImage(
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
    expect(screen.getByRole("button", { name: "Prepare the image" }).hasAttribute("disabled")).toBe(
      true,
    )
  })

  it("lists the fields the prepared image declares", async () => {
    await prepareImage(
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
    expect(screen.queryByRole("list", { name: "Subscription logins" })).toBeNull()
  })

  it("lists each login the prepared image declares, after its fields", async () => {
    await prepareImage({
      ...preparation({ state: "succeeded" }),
      "image.describe": () => ({ ...vanilla, logins: [codex] }),
    })

    const logins = await screen.findByRole("list", { name: "Subscription logins" })
    const row = within(within(logins).getByRole("listitem", { name: "Codex" }))
    expect(row.getByText("Signs Codex in with your ChatGPT plan.")).toBeDefined()
    expect(row.getByText("After creation")).toBeDefined()
    expect(
      screen.getByRole("list", { name: "Setup fields" }).compareDocumentPosition(logins) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  // jsdom lays nothing out, so the line clamp shows only in the classes.
  it("shows each field's whole description, however narrow the screen", async () => {
    await prepareImage(
      preparation({
        state: "succeeded",
        steps: [{ name: "image", state: "succeeded", detail: "Building the image." }],
      }),
    )

    const fields = within(await screen.findByRole("list", { name: "Setup fields" }))
    const description = fields.getByText("Instructions the instance follows in every turn.")
    expect(description.className).not.toMatch(/line-clamp-\d/)
  })

  it("shows each login's whole description, however narrow the screen", async () => {
    await prepareImage({
      ...preparation({ state: "succeeded" }),
      "image.describe": () => ({ ...vanilla, logins: [codex] }),
    })

    const logins = within(await screen.findByRole("list", { name: "Subscription logins" }))
    const description = logins.getByText("Signs Codex in with your ChatGPT plan.")
    expect(description.className).not.toMatch(/line-clamp-\d/)
  })

  it("marks the step that failed with what went wrong", async () => {
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

    expect(step("describe").getByText("Failed")).toBeDefined()
    expect(step("describe").getByText('Executable "claude" is not on PATH.')).toBeDefined()
    expect(step("image").getByText("Done")).toBeDefined()
    expect(screen.queryByRole("list", { name: "Setup fields" })).toBeNull()
    expect(screen.getByRole("button", { name: "Prepare the image" }).hasAttribute("disabled")).toBe(
      false,
    )
  })

  it("says why when the preparation could not start", async () => {
    await prepareImage({})

    expect((await screen.findByRole("alert")).textContent).toContain(
      "The stub has no answer for image.prepare.",
    )
    expect(screen.getByRole("button", { name: "Prepare the image" }).hasAttribute("disabled")).toBe(
      false,
    )
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
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Prepare the image" }))
    await act(() => clock.advance(0))

    const again = screen.getByRole("button", { name: "Prepare the image" })
    expect(again.hasAttribute("disabled")).toBe(false)
    await user.click(again)
    await act(() => clock.advance(0))

    expect(
      step("image").getByText("Building the image, or reusing the one prepared."),
    ).toBeDefined()
    expect(step("image").getByText("Running")).toBeDefined()
    expect(again.hasAttribute("disabled")).toBe(true)
    expect(caller.calls.filter((call) => call.method === "image.prepare")).toEqual([
      { method: "image.prepare", params: {} },
      { method: "image.prepare", params: {} },
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
    ...answers,
  })
}

/** Open the onboarding, prepare the image, and go on to the role step. */
async function openOnboarding(caller: StubCaller, onPublished = () => {}) {
  const clock = fakeClock()
  const user = userEvent.setup()
  render(<Onboarding caller={caller} clock={clock} onPublished={onPublished} />)
  await user.click(screen.getByRole("button", { name: "Prepare the image" }))
  await act(() => clock.advance(0))
  await user.click(await screen.findByRole("button", { name: "Continue" }))
  return { user, clock }
}

const next = (user: UserEvent) => user.click(screen.getByRole("button", { name: "Continue" }))

/** Pick no role, name the instance, keep the skills, and write a prompt, up to the setup step. */
async function toSetup(user: UserEvent, name = "Ada") {
  await next(user)
  await user.type(screen.getByLabelText("Name"), name)
  await next(user)
  await next(user)
  const prompt = screen.getByLabelText("Behavior prompt")
  await user.clear(prompt)
  await user.type(prompt, "Answer in haiku.")
  await next(user)
}

async function fillSetup(user: UserEvent) {
  await user.type(screen.getByLabelText("Model"), "openai:gpt-5")
  await user.type(screen.getByLabelText("API key"), "sk-private")
}

describe("the onboarding's identity step", () => {
  it("names the instance and draws the avatar picked for it", async () => {
    const { user } = await openOnboarding(hub({}))
    await next(user)

    const onward = screen.getByRole("button", { name: "Continue" })
    expect(onward.hasAttribute("disabled")).toBe(true)
    await user.type(screen.getByLabelText("Name"), "Ada")
    await user.click(screen.getByRole("button", { name: "Squircle" }))
    await user.click(screen.getByRole("button", { name: "Green" }))

    const preview = screen.getByRole("img", { name: "Avatar of Ada" })
    expect(preview.getAttribute("data-shape")).toBe("squircle")
    expect(preview.querySelector("[data-slot=avatar-fallback]")?.getAttribute("data-variant")).toBe(
      "green",
    )
    expect(onward.hasAttribute("disabled")).toBe(false)
  })

  it("starts from a circle in the palette's first color", async () => {
    const { user } = await openOnboarding(hub({}))
    await next(user)

    await user.type(screen.getByLabelText("Name"), "Ada")

    const preview = screen.getByRole("img", { name: "Avatar of Ada" })
    expect(preview.getAttribute("data-shape")).toBe("circle")
    expect(preview.querySelector("[data-slot=avatar-fallback]")?.getAttribute("data-variant")).toBe(
      "blue",
    )
  })
})

describe("the onboarding's setup step", () => {
  it("asks for each declared field, and never shows a secret it was given", async () => {
    const { user } = await openOnboarding(hub({}))
    await toSetup(user)

    const configuration = within(screen.getByRole("group", { name: "Configuration" }))
    const secrets = within(screen.getByRole("group", { name: "Secrets" }))
    expect(configuration.getByLabelText("Model").tagName).toBe("INPUT")
    expect(configuration.queryByLabelText(/Behavior prompt/)).toBeNull()
    expect(configuration.getByText("The model the instance calls.")).toBeDefined()
    expect(secrets.getByLabelText("API key").getAttribute("type")).toBe("password")
  })

  it("creates the instance from the values and the identity", async () => {
    const caller = hub({})
    const { user } = await openOnboarding(caller)
    await next(user)
    await user.type(screen.getByLabelText("Name"), "Ada")
    await user.click(screen.getByRole("button", { name: "Square" }))
    await next(user)
    await next(user)
    await user.clear(screen.getByLabelText("Behavior prompt"))
    await user.type(screen.getByLabelText("Behavior prompt"), "Answer in haiku.")
    await next(user)
    await fillSetup(user)

    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toEqual({
      manifest_id: "Ada",
      persona_name: "Ada",
      model: "openai:gpt-5",
      config: { behavior_prompt: "Answer in haiku." },
      secrets: { api_key: "sk-private" },
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
    await toSetup(user)
    await fillSetup(user)

    expect(screen.getByRole("switch", { name: /Share/ }).getAttribute("aria-checked")).toBe("false")
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toEqual({
      manifest_id: "Ada",
      persona_name: "Ada",
      model: "openai:gpt-5",
      config: { behavior_prompt: "Answer in haiku." },
      secrets: { api_key: "sk-private" },
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
    await toSetup(user)
    await fillSetup(user)

    await user.click(screen.getByRole("switch", { name: /Share/ }))
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toMatchObject({
      config: { share: true },
    })
  })

  it("renders each field by its type, starting from its default", async () => {
    const { user } = await openOnboarding(hub({ "image.describe": () => typed }))
    await toSetup(user)

    const configuration = within(screen.getByRole("group", { name: "Configuration" }))
    expect(configuration.getByRole("combobox", { name: "Tone" }).textContent).toContain("plain")
    expect(configuration.getByRole("switch", { name: /Review/ }).getAttribute("aria-checked")).toBe(
      "true",
    )
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
    await toSetup(user)
    await fillSetup(user)

    await user.click(screen.getByRole("combobox", { name: "Tone" }))
    await user.click(await screen.findByRole("option", { name: "formal" }))
    await user.click(screen.getByRole("switch", { name: /Review/ }))
    const steps = screen.getByRole("spinbutton", { name: /Steps per turn/ })
    await user.clear(steps)
    await user.type(steps, "12")
    await user.click(screen.getByRole("button", { name: "Create instance" }))

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
    await toSetup(user)
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
    await toSetup(user)
    await fillSetup(user)
    const email = screen.getByLabelText(/Commit email/)
    const repository = screen.getByLabelText(/Repository/)
    await user.clear(email)
    await user.type(email, "not-an-email")
    await user.clear(repository)
    await user.type(repository, "not a repository")

    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(caller.calls.find((call) => call.method === "instance.create")?.params).toMatchObject({
      config: { commit_email: "not-an-email", repository: "not a repository" },
    })
    const errors = within(await screen.findByRole("group", { name: "Configuration" }))
      .getAllByRole("alert")
      .map((alert) => alert.textContent)
    expect(errors).toEqual([
      "Commit email is not an email address, like someone@example.com.",
      "Repository is not a URL with a scheme and a host, like https://example.com.",
    ])
    expect(screen.getByLabelText(/Commit email/).getAttribute("aria-invalid")).toBe("true")
    expect(screen.getByLabelText(/Repository/).getAttribute("aria-invalid")).toBe("true")
  })

  it("shows the fields a rebuilt image stored, and a retry sends the new one", async () => {
    let described = 0
    let creates = 0
    const rebuilt: DeclaredSetup = {
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
      "image.describe": () => {
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
    const { user } = await openOnboarding(caller)
    await toSetup(user)
    await fillSetup(user)

    await user.click(screen.getByRole("button", { name: "Create instance" }))
    await user.click(await screen.findByRole("button", { name: "Back to setup" }))

    expect((await screen.findByLabelText("Search token")).getAttribute("type")).toBe("password")
    for (const _ of ["prompt", "skills", "identity", "role", "image"]) {
      await user.click(screen.getByRole("button", { name: "Back" }))
    }
    const asked = within(screen.getByRole("list", { name: "Setup fields" }))
    expect(asked.getByRole("listitem", { name: "Search token" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Prepare the image" }).hasAttribute("disabled")).toBe(
      true,
    )
    for (const _ of ["role", "identity", "skills", "prompt", "setup"]) await next(user)

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

async function pickCoder(user: UserEvent) {
  await user.click(await screen.findByRole("combobox", { name: "Factory role" }))
  await user.click(await screen.findByRole("option", { name: "coder in software" }))
  await next(user)
}

async function fillCoderSetup(user: UserEvent) {
  await user.type(screen.getByLabelText("Model"), "anthropic:claude-sonnet-5")
  await user.type(screen.getByLabelText("API key"), "sk-ant")
  await user.type(screen.getByLabelText("Repository"), "https://github.com/owner/billing.git")
  await user.type(screen.getByLabelText("GitHub token"), "ghp-test")
}

const sent = (caller: StubCaller) =>
  caller.calls.filter((call) => call.method === "instance.create").map((call) => call.params)

const ticked = (name: string) =>
  screen.getByRole("checkbox", { name }).getAttribute("aria-checked") === "true"

describe("the onboarding's role, skills and prompt steps", () => {
  it("creates a vanilla instance with no role, the skills ticked and the prompt written", async () => {
    const caller = hub({ "library.list": () => ({ entries: [skill("pr"), skill("tdd")] }) })
    const { user } = await openOnboarding(caller)
    await next(user)
    await user.type(screen.getByLabelText("Name"), "Ada")
    await next(user)

    expect([ticked("pr"), ticked("tdd")]).toEqual([false, false])
    await user.click(await screen.findByRole("checkbox", { name: "tdd" }))
    await next(user)
    const prompt = screen.getByLabelText("Behavior prompt") as HTMLTextAreaElement
    expect(prompt.value).toBe("You are a personal AI teammate.\n")
    await user.clear(prompt)
    await user.type(prompt, "Answer in haiku.")
    await next(user)
    await fillSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(sent(caller)).toEqual([
      {
        manifest_id: "Ada",
        persona_name: "Ada",
        model: "openai:gpt-5",
        config: { behavior_prompt: "Answer in haiku." },
        secrets: { api_key: "sk-private" },
        avatar: { shape: "circle", color: "blue" },
        picks: ["tdd"],
      },
    ])
  })

  it("preselects the coder's suggested picks and asks for its setup fields", async () => {
    const { user } = await openOnboarding(softwareHub())
    await pickCoder(user)
    await next(user)

    await screen.findByRole("checkbox", { name: "tdd" })
    expect(SUGGESTED.filter(ticked)).toEqual(SUGGESTED)
    expect(ticked("house-style")).toBe(false)
    await next(user)
    await next(user)
    const configuration = within(screen.getByRole("group", { name: "Configuration" }))
    const secrets = within(screen.getByRole("group", { name: "Secrets" }))
    expect(configuration.getByLabelText("Repository")).toBeDefined()
    expect(secrets.getByLabelText("GitHub token").getAttribute("type")).toBe("password")
  })

  it("creates a coder without a suggested pick the user unticked", async () => {
    const caller = softwareHub()
    const { user } = await openOnboarding(caller)
    await pickCoder(user)
    await next(user)

    await user.click(await screen.findByRole("checkbox", { name: "tdd" }))
    await next(user)
    await next(user)
    await fillCoderSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

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
        avatar: { shape: "circle", color: "blue" },
        picks: SUGGESTED.filter((name) => name !== "tdd"),
      },
    ])
  })

  it("starts the prompt from the role's, and creates the coder with the edited text", async () => {
    const caller = softwareHub()
    const { user } = await openOnboarding(caller)
    await pickCoder(user)
    await next(user)
    await next(user)

    const prompt = screen.getByLabelText("Behavior prompt") as HTMLTextAreaElement
    expect(prompt.value).toBe("You implement tickets.\n")
    await user.type(prompt, "Keep each pull request small.")
    await next(user)
    await fillCoderSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

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
    await next(user)
    await next(user)
    await next(user)
    await fillCoderSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(sent(caller)[0]).toMatchObject({
      manifest_id: "billing-coder",
      persona_name: "billing-coder",
    })
  })

  it("asks for the fields of a role edited since it was picked, once the hub refuses", async () => {
    const token = {
      name: "CLAUDE_CODE_OAUTH_TOKEN",
      label: "Claude Code token",
      description: "Signs Claude Code in.",
      kind: "secret" as const,
      type: "text" as const,
      required: true,
    }
    let describes = 0
    let creates = 0
    const caller = softwareHub({
      "factory.describe": () => {
        describes += 1
        const setup = describes === 1 ? coder : { setup_fields: [...coder.setup_fields, token] }
        return { instances: { coder: setup }, suggested_picks: { coder: SUGGESTED } }
      },
      "instance.create": () => {
        creates += 1
        if (creates === 1) {
          throw new CallError({
            code: "INVALID_SETUP",
            message: "Some setup values are missing or invalid.",
            retryable: false,
            fields: { CLAUDE_CODE_OAUTH_TOKEN: "Claude Code token is required." },
          })
        }
        return { operation_id: "op-create", instance_id: "instance-2" }
      },
    })
    const { user } = await openOnboarding(caller)
    await pickCoder(user)
    for (const _ of ["identity", "skills", "prompt"]) await next(user)
    await fillCoderSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    const refused = await screen.findByLabelText("Claude Code token")
    expect(refused.getAttribute("aria-invalid")).toBe("true")
    await user.type(refused, "claude-token")
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(sent(caller)[1]).toMatchObject({
      secrets: { api_key: "sk-ant", GH_TOKEN: "ghp-test", CLAUDE_CODE_OAUTH_TOKEN: "claude-token" },
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
    await next(user)
    await next(user)
    await next(user)
    await fillCoderSetup(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(
      await screen.findByText('An instance named "software-coder" exists already.'),
    ).toBeDefined()
    for (const _ of ["prompt", "skills", "identity"]) {
      await user.click(screen.getByRole("button", { name: "Back" }))
    }
    await user.clear(screen.getByLabelText("Name"))
    await user.type(screen.getByLabelText("Name"), "billing-coder")
    for (const _ of ["identity", "skills", "prompt"]) await next(user)
    await user.click(screen.getByRole("button", { name: "Create instance" }))

    expect(sent(caller)).toMatchObject([
      { manifest_id: "software-coder" },
      { manifest_id: "billing-coder" },
    ])
    expect(await screen.findByRole("list", { name: "Creation steps" })).toBeDefined()
  })
})

describe("the onboarding's start step", () => {
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
    await toSetup(user)
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

describe("the onboarding's sign in step", () => {
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
    await toSetup(user)
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
