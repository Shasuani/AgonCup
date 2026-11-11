// Brand fonts, self-hosted (bundled into /assets): the production CSP is
// font-src/style-src 'self', so Google Fonts never loaded there.
import "@fontsource/cinzel/latin-600.css";
import "@fontsource/cinzel/latin-700.css";
import "@fontsource/barlow/latin-400.css";
import "@fontsource/barlow/latin-500.css";
import "@fontsource/barlow/latin-600.css";
import "@fontsource/barlow/latin-700.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import "./styles/global.css";
import "./styles/hextech.css";
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("#root element not found");
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
