/**
 * src/main.tsx — React entry point. Mounts <App/> under #root with the hash router.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { RouterProvider } from "./router";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider>
      <App />
    </RouterProvider>
  </React.StrictMode>,
);
