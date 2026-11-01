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
} else if (window.location.pathname.startsWith("/proto/thread")) {
  // PROTOTYPE: /proto/thread renders the throwaway thread prototype inside the shell.
  void import("./proto/thread").then(({ ThreadPrototype }) => {
    createRoot(root).render(
      <StrictMode>
        <ThreadPrototype />
      </StrictMode>,
    )
  })
} else if (window.location.pathname.startsWith("/proto/panes")) {
  // PROTOTYPE: /proto/panes renders the throwaway side-panes prototype beside the thread.
  void import("./proto/panes").then(({ PanesPrototype }) => {
    createRoot(root).render(
      <StrictMode>
        <PanesPrototype />
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
