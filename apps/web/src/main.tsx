import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

// PROTOTYPE: /proto/shell renders the throwaway shell prototype on mock data, without a hub.
if (window.location.pathname.startsWith("/proto/shell")) {
  void import("./proto/shell").then(({ Shell }) => {
    createRoot(root).render(
      <StrictMode>
        <Shell />
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
