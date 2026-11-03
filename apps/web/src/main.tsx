import { browserTransport, createClient } from "@kinby/contract"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"

const root = document.getElementById("root")
if (root === null) throw new Error("index.html has no #root element")

// PROTOTYPE: /proto/* routes render throwaway prototypes on mock data, no hub.
if (window.location.pathname.startsWith("/proto/onboarding")) {
  void import("./proto/onboarding").then(({ OnboardingPrototype }) => {
    createRoot(root).render(
      <StrictMode>
        <OnboardingPrototype />
      </StrictMode>,
    )
  })
} else if (window.location.pathname.startsWith("/proto/library")) {
  void import("./proto/library").then(({ LibraryPrototype }) => {
    createRoot(root).render(
      <StrictMode>
        <LibraryPrototype />
      </StrictMode>,
    )
  })
} else if (window.location.pathname.startsWith("/proto/instance")) {
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
