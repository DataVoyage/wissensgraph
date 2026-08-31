/** Einsprungpunkt der SPA. */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./index.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Wurzelelement #root fehlt in index.html.");
}

// Der Query-Cache ist der einzige Ort, an dem Server-Zustand liegt (§17.1). Kein automatisches
// Nachladen beim Fokuswechsel: Die Oberfläche lädt nach jeder Kuration ohnehin neu, und ein
// zusätzlicher Schwung Anfragen bei jedem Fensterwechsel wäre Last ohne Erkenntnis.
const client = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
