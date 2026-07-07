// Registers jest-dom matchers (toBeInTheDocument, toHaveStyle, …) and clears
// mocks between tests. Loaded via vitest.config.ts `setupFiles`.
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
