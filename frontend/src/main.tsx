import "katex/dist/katex.min.css";
import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { LucideProvider } from "lucide-react";

import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Bộ icon Lucide dùng chung: 16 px, nét 1.75 (ghi đè cỡ bằng class Tailwind h-4, w-5…). */}
    <LucideProvider size={16} strokeWidth={1.75}>
      <App />
    </LucideProvider>
  </StrictMode>,
);
