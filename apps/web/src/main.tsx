import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

// PROTOTYPE: /proto/* renders a throwaway design board, without the app (and its theme store,
// which would put .dark on <html> and darken the board's light panels).
if (window.location.pathname.startsWith("/proto/")) {
  void import("./proto/foundations").then(({ Foundations }) => {
    createRoot(root).render(
      <StrictMode>
        <Foundations />
      </StrictMode>,
    )
  })
} else {
  void import("./App.tsx").then(({ default: App }) => {
    const client = createClient(window.location.origin, browserTransport)
    createRoot(root).render(
      <StrictMode>
        <App client={client} />
      </StrictMode>,
    )
  })
}
