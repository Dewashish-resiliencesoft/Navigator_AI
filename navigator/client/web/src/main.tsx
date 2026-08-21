import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { installRefreshGuards } from "./lib/tabPersistence";
import "./index.css";

installRefreshGuards();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
