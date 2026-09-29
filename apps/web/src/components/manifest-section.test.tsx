import { CallError } from "@kinby/contract"
import type { ManifestResult, ManifestSetCommand } from "@kinby/contract"
import { type Answers, stubCaller } from "@kinby/contract/testing"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ManifestSection } from "@/components/manifest-section"

const manifest: ManifestResult = {
  values: {
    persona_name: "Ada",
    models: { main: "openai:gpt-5", recap: null, embed: null },
    budgets: { steps: null, tokens: null, seconds: null, usd_per_day: null },
    routines: { timezone: "UTC" },
    tools: { defaults: true, bash_timeout_seconds: 120 },
    memory: { recap: "every-turn" },
    feedback: { ask: "every-turn" },
  },
  model_choices: [
    { model: "anthropic:claude-sonnet-5", priced_from: "shipped", key_set: false },
    { model: "openai:gpt-5", priced_from: "shipped", key_set: true },
  ],
  hash: "hash-1",
}

function openSection(answers: Answers = {}) {
  const caller = stubCaller({
    "manifest.get": () => manifest,
    // As the instance does: every price it wrote is a model it offers.
    "manifest.set": ({ values, prices = {} }) => ({
      values,
      model_choices: [
        ...manifest.model_choices,
        ...Object.keys(prices).map((model) => ({
          model,
          priced_from: "manifest" as const,
          key_set: false,
        })),
      ],
      hash: "hash-2",
    }),
    "config.history": () => ({ changes: [] }),
    ...answers,
  })
  render(<ManifestSection client={caller} />)
  return { caller, user: userEvent.setup() }
}

function sent(caller: ReturnType<typeof openSection>["caller"]): ManifestSetCommand[] {
  return caller.calls
    .filter((call) => call.method === "manifest.set")
    .map((call) => call.params as ManifestSetCommand)
}

describe("ManifestSection", () => {
  it("offers only priced models, marks a missing key, and saves the one picked", async () => {
    const { caller, user } = openSection()

    await user.click(await screen.findByRole("combobox", { name: "Main model" }))
    const options = await screen.findAllByRole("option")
    expect(options.map((option) => option.textContent)).toEqual([
      "anthropic:claude-sonnet-5No key",
      "openai:gpt-5",
    ])
    await user.click(options[0])
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText(/^Saved\./)).toBeDefined()
    expect(sent(caller)).toEqual([
      {
        values: {
          ...manifest.values,
          models: { main: "anthropic:claude-sonnet-5", recap: null, embed: null },
        },
        prices: {},
        hash: "hash-1",
      },
    ])
  })

  it("adds a model with its prices and sends them as a new price", async () => {
    const { caller, user } = openSection()

    await user.type(await screen.findByRole("textbox", { name: "Model" }), "mistral:large-3")
    await user.type(screen.getByRole("spinbutton", { name: "Input price" }), "2")
    await user.type(screen.getByRole("spinbutton", { name: "Output price" }), "6")
    await user.click(screen.getByRole("button", { name: "Add a model" }))
    await user.click(screen.getByRole("combobox", { name: "Main model" }))
    await user.click(await screen.findByRole("option", { name: /mistral:large-3/ }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    await screen.findByText(/^Saved\./)
    expect(sent(caller)[0]).toMatchObject({
      values: { models: { main: "mistral:large-3" } },
      prices: { "mistral:large-3": { input: 2, output: 6 } },
    })
  })

  it("refuses to add a model that is not provider:model", async () => {
    const { user } = openSection()

    await user.type(await screen.findByRole("textbox", { name: "Model" }), "large-3")
    await user.type(screen.getByRole("spinbutton", { name: "Input price" }), "2")
    await user.type(screen.getByRole("spinbutton", { name: "Output price" }), "6")

    expect(screen.getByRole("button", { name: "Add a model" })).toHaveProperty("disabled", true)
  })

  it("sends the budgets as numbers, a blank one as no limit, and the picked timezone", async () => {
    const { caller, user } = openSection({
      "manifest.get": () => ({
        ...manifest,
        values: { ...manifest.values, budgets: { ...manifest.values.budgets, tokens: 5000 } },
      }),
    })

    await user.type(await screen.findByRole("spinbutton", { name: "Steps" }), "20")
    await user.clear(screen.getByRole("spinbutton", { name: "Tokens" }))
    await user.type(screen.getByRole("spinbutton", { name: "Dollars per day" }), "2.5")
    const timezone = screen.getByRole("combobox", { name: "Routines timezone" })
    await user.clear(timezone)
    await user.type(timezone, "Madrid")
    await user.click(await screen.findByRole("option", { name: "Europe/Madrid" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    await screen.findByText(/^Saved\./)
    expect(sent(caller)[0].values).toMatchObject({
      budgets: { steps: 20, tokens: null, seconds: null, usd_per_day: 2.5 },
      routines: { timezone: "Europe/Madrid" },
    })
  })

  it("shows the instance's reason next to the value it refused", async () => {
    const { user } = openSection({
      "manifest.set": () => {
        throw new CallError({
          code: "INVALID_ARGUMENT",
          message: "Some values are invalid.",
          retryable: false,
          fields: { "tools.bash_timeout_seconds": "Input should be greater than 0" },
        })
      },
    })

    const timeout = await screen.findByRole("spinbutton", { name: "Shell timeout, seconds" })
    await user.clear(timeout)
    await user.type(timeout, "0")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("Input should be greater than 0")).toBeDefined()
    expect(timeout.getAttribute("aria-invalid")).toBe("true")
  })

  it("offers to load theirs when kinby.toml changed since it was opened", async () => {
    let theirs = false
    const { caller, user } = openSection({
      "manifest.get": () =>
        theirs
          ? { ...manifest, values: { ...manifest.values, persona_name: "Grace" }, hash: "hash-3" }
          : manifest,
      "manifest.set": () => {
        theirs = true
        throw new CallError({ code: "STALE", message: "kinby.toml changed.", retryable: false })
      },
    })

    const persona = await screen.findByRole("textbox", { name: "Persona name" })
    await user.clear(persona)
    await user.type(persona, "Ida")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Changed since you opened it")
    await user.click(within(alert).getByRole("button", { name: "Load theirs" }))

    expect(await screen.findByDisplayValue("Grace")).toBe(persona)
    await user.clear(persona)
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(sent(caller).at(-1)).toMatchObject({
      values: { persona_name: null },
      hash: "hash-3",
    })
  })

  it("leaves the settings the hub owns out of the form", async () => {
    openSection()

    await screen.findByRole("combobox", { name: "Main model" })
    for (const owned of [/^id$/i, /state dir/i, /listen/i, /workspace/i, /package/i]) {
      expect(screen.queryByLabelText(owned)).toBeNull()
    }
  })
})
