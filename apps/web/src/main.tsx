import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

// PROTOTYPE: /proto/settings renders the throwaway Settings dialog prototype on mock data, no hub.
if (window.location.pathname.startsWith("/proto/settings")) {
  void import("./proto/settings").then(({ SettingsPrototype }) => {
    createRoot(root).render(
      <StrictMode>
        <SettingsPrototype />
      </StrictMode>,
    )
  })
} else {
  const client = createClient(window.location.origin, browserTransport)
  createRoot(root).render(
    <StrictMode>
      <App client={client} />
    </StrictMode>,
  )
}
