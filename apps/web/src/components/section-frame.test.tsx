import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { SectionFrame } from "@/components/section-frame"
import { Button } from "@/components/ui/button"

function frame(variant?: "dialog" | "page") {
  render(
    <SectionFrame
      title="Browser profiles"
      lead="The sites Ada opens signed in."
      actions={<Button>New profile</Button>}
      variant={variant}
    >
      <p>LinkedIn</p>
    </SectionFrame>,
  )
}

const header = () => screen.getByRole("region").querySelector("header")?.className

describe("a section frame", () => {
  it("frames a page's section by its title and lead, with its action over its rows", () => {
    frame("page")

    const section = screen.getByRole("region", { name: "Browser profiles" })
    expect(section.getAttribute("aria-describedby")).toBe(
      screen.getByText("The sites Ada opens signed in.").id,
    )
    expect(screen.getByRole("button", { name: "New profile" })).toBeDefined()
    expect(screen.getByText("LinkedIn")).toBeDefined()
  })

  // jsdom lays nothing out, so the room left for the dialog's Close shows only in the classes.
  it("leaves the dialog's Close room out of a page's section", () => {
    frame("page")
    const page = header()
    cleanup()
    frame()

    expect(header()).toMatch(/\bpr-16\b/)
    expect(page).not.toMatch(/\bpr-16\b/)
  })

  describe("on a phone", () => {
    let wide: typeof window.matchMedia
    beforeEach(() => {
      wide = window.matchMedia
      window.matchMedia = (query) =>
        Object.assign(wide(query), { matches: query === "(max-width: 767px)" })
    })
    afterEach(() => {
      window.matchMedia = wide
    })

    it("leads a Settings section with Back", () => {
      frame()

      expect(screen.getByRole("button", { name: "Back" })).toBeDefined()
    })

    it("offers no Back on a page's section", () => {
      frame("page")

      expect(screen.queryByRole("button", { name: "Back" })).toBeNull()
    })
  })
})
