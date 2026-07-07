import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

// Flat config for React 18 + TypeScript + Vite. Type-aware linting is left off
// on purpose (tsc --noEmit in `npm run build` already does full type checking);
// this keeps lint fast and focused on hooks correctness + obvious mistakes.
export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2020,
      globals: { ...globals.browser },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  // Test files also run under Node/Vitest globals.
  {
    files: ["**/*.test.{ts,tsx}", "src/test/**"],
    languageOptions: {
      globals: { ...globals.node, ...globals.vitest },
    },
  },
);
