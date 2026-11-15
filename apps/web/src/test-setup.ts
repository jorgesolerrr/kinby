import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

import { forgetDrafts } from "@/lib/drafts"

// jsdom has no matchMedia, and the theme and the sidebar read it. The fake screen is wide and light.
window.matchMedia = (query) =>
  Object.assign(new EventTarget(), {
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
  })

afterEach(() => {
  cleanup()
  forgetDrafts()
})
