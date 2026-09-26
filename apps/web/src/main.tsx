import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { ConfigPrototype } from "./prototype/config"
import { MemoryPrototype } from "./prototype/memory"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

const client = createClient(window.location.origin, browserTransport)

createRoot(root).render(
  <StrictMode>
    {/* PROTOTYPE, throwaway: the config and memory variants, no hub needed. */}
    {window.location.pathname === "/prototype/config" ? (
      <ConfigPrototype />
    ) : window.location.pathname === "/prototype/memory" ? (
      <MemoryPrototype />
    ) : (
      <App client={client} />
    )}
  </StrictMode>,
)
