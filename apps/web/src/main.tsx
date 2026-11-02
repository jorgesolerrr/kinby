import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

// PROTOTYPE: /proto/instance renders the throwaway instance page prototype on mock data, no hub.
if (window.location.pathname.startsWith("/proto/instance")) {
  void import("./proto/instance").then(({ InstancePrototype }) => {
    createRoot(root).render(
      <StrictMode>
        <InstancePrototype />
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
