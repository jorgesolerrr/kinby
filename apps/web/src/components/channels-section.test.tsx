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

const row = async (name: string) => within(await screen.findByRole("listitem", { name }))

/** Unfold a channel's details under its row. */
async function unfold(user: Opened["user"], name: string) {
  const channel = await row(name)
  await user.click(channel.getByRole("button", { name: "Details" }))
  return channel
}

const listedNames = () =>
  screen
    .queryAllByRole("listitem")
    .map((item) => item.getAttribute("aria-label"))
    .filter((name) => name !== null)

const addForm = () => within(screen.getByRole("form", { name: "Add a channel" }))

async function openAdd(user: Opened["user"]) {
  await user.click(await screen.findByRole("button", { name: "Add channel" }))
  return addForm()
}

async function pickKind(user: Opened["user"], kind: string) {
  await user.click(addForm().getByRole("combobox", { name: "Kind" }))
  await user.click(await screen.findByRole("option", { name: kind }))
}

async function addTelegram(user: Opened["user"]) {
  const form = await openAdd(user)
  await pickKind(user, "Telegram")
  await user.type(form.getByLabelText("Name"), "Phone")
  await user.type(form.getByLabelText(/^Bot token/), "bot-token")
  await user.click(form.getByRole("button", { name: "Add channel" }))
  return row("Phone")
}

describe("ChannelsSection", () => {
  it("says what channels are for when the hub has none, and points at Add channel", async () => {
    openPage()

    expect(await screen.findByText("No channels yet")).toBeTruthy()
    expect(screen.getByText(/notifications to you .* Add one with Add channel\./)).toBeTruthy()
  })

  it("shows each channel's kind and the first status that holds, a failure only until a delivery", async () => {
    openPage([
      channel({
        channel_id: "off-id",
        name: "Off",
        kind: "telegram",
        enabled: false,
        pairing_code: "654321",
      }),
      channel({
        channel_id: "phone-id",
        name: "Phone",
        kind: "telegram",
        pairing_code: "123456",
        last_failure: { at: "2026-10-24T12:00:00Z", error: "401 Unauthorized" },
      }),
      channel({
        channel_id: "broken-id",
        name: "Broken",
        kind: "email",
        last_sent_at: "2026-10-24T11:00:00Z",
        last_failure: { at: "2026-10-24T12:00:00Z", error: "401 Unauthorized" },
      }),
      channel({
        channel_id: "recovered-id",
        name: "Recovered",
        kind: "email",
        last_sent_at: "2026-10-24T13:00:00Z",
        last_failure: { at: "2026-10-24T12:00:00Z", error: "401 Unauthorized" },
      }),
      channel({
        channel_id: "mail-id",
        name: "Inbox",
        kind: "email",
        last_sent_at: "2026-10-24T11:00:00Z",
      }),
      channel({ channel_id: "new-id", name: "New", kind: "email" }),
    ])

    expect((await row("Off")).getByText("Telegram · Disabled")).toBeTruthy()
    expect((await row("Phone")).getByText("Telegram · Waiting for 123456")).toBeTruthy()
    expect((await row("Broken")).getByText(/^Email · Failed \w+ \d+/)).toBeTruthy()
    expect((await row("Recovered")).getByText(/^Email · Sent \w+ \d+/)).toBeTruthy()
    expect((await row("Inbox")).getByText(/^Email · Sent \w+ \d+/)).toBeTruthy()
    expect((await row("New")).getByText("Email · Nothing sent yet")).toBeTruthy()
  })

  it("disables a channel and enables it again from its row", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await row("Inbox")
    const enabled = inbox.getByRole("switch", { name: "Enabled" })
    await user.click(enabled)
    await waitFor(() => expect(enabled.getAttribute("aria-checked")).toBe("false"))
    expect(inbox.getByText("Email · Disabled")).toBeTruthy()
    await user.click(enabled)
    await waitFor(() => expect(enabled.getAttribute("aria-checked")).toBe("true"))

    expect(writes(caller)).toEqual([
      { method: "channel.update", params: { channel_id: "mail-id", enabled: false } },
      { method: "channel.update", params: { channel_id: "mail-id", enabled: true } },
    ])
  })

  it("keeps the details folded until the chevron unfolds them, one channel at a time", async () => {
    const { user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
      channel({ channel_id: "work-id", name: "Work", kind: "email" }),
    ])

    const inbox = await row("Inbox")
    expect(inbox.queryByText("Last delivery")).toBeNull()
    await unfold(user, "Inbox")
    expect(inbox.getByText("Last delivery")).toBeTruthy()

    const work = await unfold(user, "Work")
    expect(work.getByText("Last delivery")).toBeTruthy()
    await waitFor(() => expect(inbox.queryByText("Last delivery")).toBeNull())
  })

  it("shows a Discord channel the interactions endpoint URL to paste, and copies it", async () => {
    const { user } = openPage([
      channel({ channel_id: "discord-id", name: "DMs", kind: "discord", paired: false }),
    ])
    const url = `${window.location.origin}/channels/discord-id/discord`

    const dms = await unfold(user, "DMs")
    expect(dms.getByLabelText("Interactions endpoint URL").getAttribute("value")).toBe(url)
    await user.click(dms.getByRole("button", { name: "Copy" }))

    expect(await navigator.clipboard.readText()).toBe(url)
  })

  it("shows the last delivery and the last failure with its error", async () => {
    const { user } = openPage([
      channel({
        channel_id: "mail-id",
        name: "Inbox",
        kind: "email",
        last_sent_at: "2026-10-24T11:00:00Z",
        last_failure: { at: "2026-10-24T12:00:00Z", error: "401 Unauthorized" },
      }),
    ])

    const inbox = await unfold(user, "Inbox")
    expect(inbox.getByText("Last delivery")).toBeTruthy()
    expect(inbox.getByText("Last failure")).toBeTruthy()
    expect(inbox.getByText(/401 Unauthorized/)).toBeTruthy()
  })

  it("sends a test and shows the delivery", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await unfold(user, "Inbox")
    await user.click(inbox.getByRole("button", { name: "Send test" }))

    await waitFor(() => expect(inbox.queryByText("Email · Nothing sent yet")).toBeNull())
    expect(inbox.getByText(/^Email · Sent /)).toBeTruthy()
    expect(writes(caller)).toEqual([{ method: "channel.test", params: { channel_id: "mail-id" } }])
  })

  it("sends no test from a disabled channel", async () => {
    const { user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email", enabled: false }),
    ])

    const inbox = await unfold(user, "Inbox")

    expect(inbox.getByRole("button", { name: "Send test" }).hasAttribute("disabled")).toBe(true)
  })

  it("renames a channel in place", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await unfold(user, "Inbox")
    await user.click(inbox.getByRole("button", { name: "Rename" }))
    const name = inbox.getByRole("textbox", { name: "New name" })
    await user.clear(name)
    await user.type(name, "Work inbox")
    await user.click(inbox.getByRole("button", { name: "Save" }))

    expect(await row("Work inbox")).toBeTruthy()
    expect(writes(caller)).toEqual([
      { method: "channel.update", params: { channel_id: "mail-id", name: "Work inbox" } },
    ])
  })

  it("deletes a channel after a confirm", async () => {
    const { caller, user } = openPage([
      channel({ channel_id: "mail-id", name: "Inbox", kind: "email" }),
    ])

    const inbox = await unfold(user, "Inbox")
    await user.click(inbox.getByRole("button", { name: "Delete" }))
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

    const inbox = await unfold(user, "Inbox")
    expect(inbox.getByText("Ada")).toBeTruthy()
    await user.click(inbox.getByRole("button", { name: "Unmute Ada" }))

    await waitFor(() => expect(inbox.queryByText("Ada")).toBeNull())
    expect(inbox.getByText("Mutes no instance")).toBeTruthy()
    expect(writes(caller)).toEqual([
      { method: "channel.unmute", params: { channel_id: "mail-id", instance_id: "coder-id" } },
    ])
  })

  it("opens the add form at the top of the list on its kind, and closes it on Cancel", async () => {
    const { user } = openPage([channel({ channel_id: "mail-id", name: "Inbox", kind: "email" })])

    const form = await openAdd(user)
    const add = screen.getAllByRole("button", { name: "Add channel" })[0]

    expect(document.activeElement).toBe(form.getByRole("combobox", { name: "Kind" }))
    expect(add.hasAttribute("disabled")).toBe(true)
    await user.click(form.getByRole("button", { name: "Cancel" }))
    expect(screen.queryByRole("form", { name: "Add a channel" })).toBeNull()
    expect(add.hasAttribute("disabled")).toBe(false)
  })

  it("asks for the fields the picked kind describes, its secrets as passwords", async () => {
    const { user } = openPage()

    const form = await openAdd(user)
    await pickKind(user, "Email")

    expect(form.getByLabelText(/^To/).getAttribute("type")).toBe("email")
    expect(form.getByLabelText(/^API key/).getAttribute("type")).toBe("password")
    expect(form.queryByLabelText(/^Bot token/)).toBeNull()
  })

  it("adds a channel with its settings and secrets, and keeps no secret on the page", async () => {
    const { caller, user } = openPage()

    const form = await openAdd(user)
    await pickKind(user, "Email")
    await user.type(form.getByLabelText("Name"), "Inbox")
    await user.type(form.getByLabelText(/^To/), "me@example.com")
    await user.type(form.getByLabelText(/^API key/), "re_secret")
    await user.click(form.getByRole("button", { name: "Add channel" }))

    expect(await row("Inbox")).toBeTruthy()
    expect(screen.queryByRole("form", { name: "Add a channel" })).toBeNull()
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

  it("shows a field the hub refused under it, and keeps the form with what was typed", async () => {
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

    const form = await openAdd(user)
    await pickKind(user, "Email")
    await user.type(form.getByLabelText("Name"), "Inbox")
    await user.type(form.getByLabelText(/^To/), "me@example")
    await user.click(form.getByRole("button", { name: "Add channel" }))

    expect(await form.findByText(/To is not an email address/)).toBeTruthy()
    expect((form.getByLabelText(/^To/) as HTMLInputElement).value).toBe("me@example")
  })

  it("pairs a new channel: its details open on the code until the channel is paired", async () => {
    const { user, clock, pair } = openPage()

    const phone = await addTelegram(user)
    expect(phone.getByText("123456")).toBeTruthy()
    expect(phone.getByText(/Valid until/)).toBeTruthy()
    expect(phone.getByRole("button", { name: "Send test" }).hasAttribute("disabled")).toBe(true)

    pair("telegram-id")
    await act(() => clock.advance(5_000))

    await waitFor(() => expect(phone.queryByText("123456")).toBeNull())
    expect(phone.getByText("Telegram · Nothing sent yet")).toBeTruthy()
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
})
