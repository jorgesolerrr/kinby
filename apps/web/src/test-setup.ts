import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

// jsdom has no matchMedia, and the theme and the sidebar read it. The fake screen is wide and light.
window.matchMedia = (query) =>
  Object.assign(new EventTarget(), {
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
  })

// jsdom lays nothing out, so it has no scrollIntoView. The search palette and the approval card call it.
Element.prototype.scrollIntoView = () => {}

afterEach(cleanup)
