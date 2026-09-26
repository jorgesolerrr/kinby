import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { CreateInstancePrototype } from "./prototype/create-instance"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

const client = createClient(window.location.origin, browserTransport)

createRoot(root).render(
  <StrictMode>
    {/* PROTOTYPE, throwaway: the create-instance variants, no hub needed. */}
    {window.location.pathname === "/prototype/create-instance" ? (
      <CreateInstancePrototype />
    ) : (
      <App client={client} />
    )}
  </StrictMode>,
)
