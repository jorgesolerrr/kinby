import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { FactoryStepsPrototype, PROTOTYPE_PATH } from "./components/factory-steps-prototype"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

// PROTOTYPE (#1450): a throwaway route on fixed data, with no hub behind it.
const prototype = import.meta.env.DEV && window.location.pathname === PROTOTYPE_PATH

createRoot(root).render(
  <StrictMode>
    {prototype ? (
      <FactoryStepsPrototype />
    ) : (
      <App client={createClient(window.location.origin, browserTransport)} />
    )}
  </StrictMode>,
)
