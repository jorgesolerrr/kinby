import { CallError } from "@kinby/contract"
import type { Channel, ChannelCreateCommand, ChannelKindDescription } from "@kinby/contract"
import type { Answers } from "@kinby/contract/testing"
import { fakeClock, instanceSummary, stubCaller } from "@kinby/contract/testing"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ChannelsSection } from "@/components/channels-section"

const telegram: ChannelKindDescription = {
  kind: "telegram",
  pairs: true,
  settings: [
    {
      name: "bot_token",
      label: "Bot token",
      description: "From BotFather.",
      kind: "secret",
      type: "text",
      required: true,
    },
  ],
}

const email: ChannelKindDescription = {
  kind: "email",
  pairs: false,
  settings: [
    {
      name: "api_key",
      label: "API key",
      description: "A Resend API key.",
      kind: "secret",
      type: "text",
      required: true,
    },
    {
      name: "to",
      label: "To",
      description: "Where notifications go.",
      kind: "config",
      type: "email",
      required: true,
    },
  ],
}

const coder = instanceSummary({
  instance_id: "coder-id",
  manifest_id: "coder",
  persona_name: "Ada",
})

function channel(
  fields: Pick<Channel, "channel_id" | "name" | "kind"> & Partial<Channel>,
): Channel {
  return { settings: {}, enabled: true, paired: false, muted: [], ...fields }
}

/**
 * A page over channels the stub keeps, which the channel methods change. A kind that pairs gets
 * the code 123456 on create. `answers` replace the stub's own.
 */
function openPage(kept: Channel[] = [], answers: Answers = {}) {
  let channels = kept
  const clock = fakeClock()
  const changed = (channelId: string, change: (each: Channel) => Channel) => {
    channels = channels.map((each) => (each.channel_id === channelId ? change(each) : each))
    return channels.find((each) => each.channel_id === channelId) as Channel
  }
  const caller = stubCaller({
    "channel.kinds": () => ({ kinds: [email, telegram] }),
    "channel.list": () => ({ channels }),
    "instance.list": () => ({ instances: [coder] }),
    "channel.create": ({ name, kind }) => {
      const pairs = kind === "telegram"
      const created = channel({
        channel_id: `${kind}-id`,
        name,
        kind,
        pairing_code: pairs ? "123456" : null,
        pairing_expires_at: pairs ? "2026-10-24T12:15:00Z" : null,
      })
      channels = [...channels, created]
      return created
    },
    "channel.update": ({ channel_id, name, enabled }) =>
      changed(channel_id, (each) => ({
        ...each,
        name: name ?? each.name,
        enabled: enabled ?? each.enabled,
      })),
    "channel.delete": ({ channel_id }) => {
      channels = channels.filter((each) => each.channel_id !== channel_id)
      return { channels }
    },
    "channel.test": ({ channel_id }) =>
      changed(channel_id, (each) => ({ ...each, last_sent_at: "2026-10-24T12:00:00Z" })),
    "channel.unmute": ({ channel_id, instance_id }) =>
      changed(channel_id, (each) => ({
        ...each,
        muted: (each.muted ?? []).filter((muted) => muted !== instance_id),
      })),
    ...answers,
  })
  render(<ChannelsSection client={caller} clock={clock} />)
  return {
    caller,
    clock,
    user: userEvent.setup(),
    pair: (channelId: string) =>
      changed(channelId, (each) => ({
        ...each,
        paired: true,
        pairing_code: null,
        pairing_expires_at: null,
      })),
  }
}

type Opened = ReturnType<typeof openPage>

const writes = (caller: Opened["caller"]) =>
  caller.calls.filter((call) => /^channel\.(create|update|delete|test|unmute)$/.test(call.method))

const card = async (name: string) => within(await screen.findByRole("listitem", { name }))

const listedNames = () =>
  screen
    .queryAllByRole("listitem")
    .map((item) => item.getAttribute("aria-label"))
    .filter((name) => name !== null)

async function pickKind(user: Opened["user"], kind: string) {
  await user.click(await screen.findByRole("combobox", { name: "Kind" }))
  await user.click(await screen.findByRole("option", { name: kind }))
}

async function addTelegram(user: Opened["user"]) {
  await pickKind(user, "telegram")
  await user.type(screen.getByLabelText("Name"), "Phone")
  await user.type(screen.getByLabelText(/^Bot token/), "bot-token")
  await user.click(screen.getByRole("button", { name: "Add channel" }))
  return card("Phone")
}

describe("ChannelsSection", () => {
  it("asks for the fields the picked kind describes, its secrets as passwords", async () => {
    const { user } = openPage()

    await pickKind(user, "email")

    expect(screen.getByLabelText(/^To/).getAttribute("type")).toBe("email")
    expect(screen.getByLabelText(/^API key/).getAttribute("type")).toBe("password")
    expect(screen.queryByLabelText(/^Bot token/)).toBeNull()
  })

  it("says when the hub has no channel yet", async () => {
    openPage()

    expect(await screen.findByText("No channels yet")).toBeTruthy()
  })

  it("adds a channel with its settings and secrets, and keeps no secret on the page", async () => {
    const { caller, user } = openPage()

    await pickKind(user, "email")
    await user.type(screen.getByLabelText("Name"), "Inbox")
    await user.type(screen.getByLabelText(/^To/), "me@example.com")
    await user.type(screen.getByLabelText(/^API key/), "re_secret")
    await user.click(screen.getByRole("button", { name: "Add channel" }))

    expect(await card("Inbox")).toBeTruthy()
    expect(screen.queryByDisplayValue("re_secret")).toBeNull()
    expect(writes(caller)).toEqual([
      {
        method: "channel.create",
        params: {
          kind: "email",
          name: "Inbox",
          settings: { to: "me@example.com" },
          secrets: { api_key: "re_secret" },
        } satisfies ChannelCreateCommand,
      },
    ])
  })

  it("shows a field the hub refused under it, and keeps what was typed", async () => {
    const { user } = openPage([], {
      "channel.create": () => {
        throw new CallError({
          code: "INVALID_SETUP",
          message: "Some setup values are missing or invalid.",
          retryable: false,
          fields: { to: "To is not an email address, like someone@example.com." },
        })
      },
    })

    await pickKind(user, "email")
    await user.type(screen.getByLabelText("Name"), "Inbox")
    await user.type(screen.getByLabelText(/^To/), "me@example")
    await user.click(screen.getByRole("button", { name: "Add channel" }))

    expect(await screen.findByText(/To is not an email address/)).toBeTruthy()
    expect((screen.getByLabelText(/^To/) as HTMLInputElement).value).toBe("me@example")
  })

  it("pairs a new channel: its code and expiry show until the channel is paired", async () => {
    const { user, clock, pair } = openPage()

    const phone = await addTelegram(user)
    expect(phone.getByText("123456")).toBeTruthy()
    expect(phone.getByText(/Valid until/)).toBeTruthy()
    expect(phone.getByRole("button", { name: "Send test" }).hasAttribute("disabled")).toBe(true)

    pair("telegram-id")
    await act(() => clock.advance(5_000))

    await waitFor(() => expect(phone.queryByText("123456")).toBeNull())
    expect(phone.getByText("Paired")).toBeTruthy()
    expect(phone.getByRole("button", { name: "Send test" }).hasAttribute("disabled")).toBe(false)
  })

  it("reads no more once every channel is paired", async () => {
    const { caller, user, clock, pair } = openPage()

    await addTelegram(user)
    pair("telegram-id")
    await act(() => clock.advance(5_000))
    const lists = () => caller.calls.filter((call) => call.method === "channel.list").length
    const listed = lists()
    await act(() => clock.advance(60_000))

    expect(lists()).toBe(listed)
  })

  it("shows a Discord channel the interactions endpoint URL to paste", async () => {
    openPage([channel({ channel_id: "discord-id", name: "DMs", kind: "discord", paired: false })])

    const dms = await card("DMs")
    expect(dms.getByLabelText("Interactions endpoint URL").getAttribute("value")).toBe(
      `${window.location.origin}/channels/discord-id/discord`,
    )
  })

  it("sends a test and shows the delivery", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await card("Inbox")
    expect(inbox.getByText("Nothing sent yet")).toBeTruthy()
    await user.click(inbox.getByRole("button", { name: "Send test" }))

    await waitFor(() => expect(inbox.queryByText("Nothing sent yet")).toBeNull())
    expect(writes(caller)).toEqual([{ method: "channel.test", params: { channel_id: "mail-id" } }])
  })

  it("shows the last delivery and the last failure with its error", async () => {
    openPage([
      channel({
        channel_id: "mail-id",
        name: "Inbox",
        kind: "email",
        last_sent_at: "2026-10-24T11:00:00Z",
        last_failure: { at: "2026-10-24T12:00:00Z", error: "401 Unauthorized" },
      }),
    ])

    const inbox = await card("Inbox")
    expect(inbox.getByText("Last delivery")).toBeTruthy()
    expect(inbox.getByText("Last failure")).toBeTruthy()
    expect(inbox.getByText(/401 Unauthorized/)).toBeTruthy()
  })

  it("disables a channel and enables it again", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await card("Inbox")
    const enabled = inbox.getByRole("switch", { name: "Enabled" })
    await user.click(enabled)
    await waitFor(() => expect(enabled.getAttribute("aria-checked")).toBe("false"))
    await user.click(enabled)
    await waitFor(() => expect(enabled.getAttribute("aria-checked")).toBe("true"))

    expect(writes(caller)).toEqual([
      { method: "channel.update", params: { channel_id: "mail-id", enabled: false } },
      { method: "channel.update", params: { channel_id: "mail-id", enabled: true } },
    ])
  })

  it("renames a channel", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await card("Inbox")
    await user.click(inbox.getByRole("button", { name: "Rename" }))
    const name = inbox.getByRole("textbox", { name: "New name" })
    await user.clear(name)
    await user.type(name, "Work inbox")
    await user.click(inbox.getByRole("button", { name: "Save" }))

    expect(await card("Work inbox")).toBeTruthy()
    expect(writes(caller)).toEqual([
      { method: "channel.update", params: { channel_id: "mail-id", name: "Work inbox" } },
    ])
  })

  it("deletes a channel after a confirm", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await card("Inbox")
    await user.click(inbox.getByRole("button", { name: "Delete Inbox" }))
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Inbox?" })
    expect(writes(caller)).toEqual([])
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))

    await waitFor(() => expect(listedNames()).toEqual([]))
    expect(writes(caller)).toEqual([
      { method: "channel.delete", params: { channel_id: "mail-id" } },
    ])
  })

  it("lists the instances a channel mutes by name, and unmutes one", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email", muted: ["coder-id"] }),
    ])

    const inbox = await card("Inbox")
    expect(inbox.getByText("Ada")).toBeTruthy()
    await user.click(inbox.getByRole("button", { name: "Unmute Ada" }))

    await waitFor(() => expect(inbox.queryByText("Ada")).toBeNull())
    expect(inbox.getByText("Mutes no instance")).toBeTruthy()
    expect(writes(caller)).toEqual([
      { method: "channel.unmute", params: { channel_id: "mail-id", instance_id: "coder-id" } },
    ])
  })
})
