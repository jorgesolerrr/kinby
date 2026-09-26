import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { DashboardPrototype } from "./prototype/dashboard"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

const client = createClient(window.location.origin, browserTransport)

createRoot(root).render(
  <StrictMode>
    {/* PROTOTYPE, throwaway: the dashboard variants, no hub needed. */}
    {window.location.pathname === "/prototype/dashboard" ? <DashboardPrototype /> : <App client={client} />}
  </StrictMode>,
)
