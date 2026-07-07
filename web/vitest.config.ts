import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest runs in jsdom with global test APIs + jest-dom matchers. `vitest run`
// (see package.json) is non-watch so it stays CI-friendly.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
