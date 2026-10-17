import { browserTransport, createClient } from "@kinby/contract"
import { lazy, StrictMode, Suspense } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

// PROTOTYPE: /prototype/* renders the design variants over fixtures, in dev builds only.
const Prototype =
  import.meta.env.DEV && window.location.pathname.startsWith("/prototype")
    ? lazy(() => import("./prototype"))
    : undefined

createRoot(root).render(
  <StrictMode>
    {Prototype ? (
      <Suspense>
        <Prototype />
      </Suspense>
    ) : (
      <App client={createClient(window.location.origin, browserTransport)} />
    )}
  </StrictMode>,
)
