import { CallError } from "@kinby/contract"
import type { Client, LibraryListResult } from "@kinby/contract"
import { fakeClock } from "@kinby/contract/testing"
import { render, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { LibraryPage } from "@/components/library-page"

export const listed: LibraryListResult = {
  entries: [
    { kind: "factory", name: "software", description: null, source: "shipped" },
    { kind: "factory", name: "tickets", description: null, source: "written" },
    { kind: "skill", name: "tdd", description: "Test first.", source: "shipped" },
    { kind: "skill", name: "unslop", description: "Cut AI tells.", source: "shipped" },
  ],
}

export const tdd = "---\nname: tdd\ndescription: Test first.\n---\nRed, green, refactor.\n"

export const commit = "0123456789abcdef0123456789abcdef01234567"

/** The Library page at `path`, on `caller`. */
export function openLibrary(caller: Pick<Client, "call">, path = "/library", clock = fakeClock()) {
  window.history.replaceState(null, "", path)
  render(<LibraryPage client={caller} clock={clock} />)
  return userEvent.setup()
}

/** What a screen reader hears for each row of `list`. */
export const names = (list: HTMLElement) =>
  within(list)
    .getAllByRole("button")
    .map((item) => item.getAttribute("aria-label"))

export const refused = (fields: Record<string, string>) =>
  new CallError({
    code: "INVALID_ARGUMENT",
    message: "Some values are invalid.",
    retryable: false,
    fields,
  })
