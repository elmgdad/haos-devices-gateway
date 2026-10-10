import "@fontsource/barlow-semi-condensed/500.css"
import "@fontsource/barlow-semi-condensed/600.css"
import "@fontsource/barlow-semi-condensed/700.css"
import "@fontsource/ibm-plex-sans/400.css"
import "@fontsource/ibm-plex-sans/500.css"
import "@fontsource/ibm-plex-sans/600.css"
import "@fontsource/ibm-plex-mono/400.css"
import "@fontsource/ibm-plex-mono/500.css"
import "./index.css"

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import App from "./App"
import { prefersDark } from "./lib"
import { FeedbackProvider } from "./ui"

function applyTheme() {
  document.documentElement.classList.toggle("dark", prefersDark())
}

applyTheme()
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme)
window.setInterval(applyTheme, 5000)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <FeedbackProvider>
      <App />
    </FeedbackProvider>
  </StrictMode>,
)
