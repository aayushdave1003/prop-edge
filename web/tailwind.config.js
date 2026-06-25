/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // base
        bg: "#0B0F14",
        "bg-deep": "#070A0E",
        // surfaces / chips
        surface: "#161C24",
        "surface-hover": "#1F2832",
        // brand accent (cyan) — branding / navigation / selection ONLY
        brand: "var(--brand)",
        // text
        ink: "#F4F7FA",
        "ink-dim": "#7E8A99",
        // active stat-filter outline
        outline: "#E7EDF2",
        // utility links
        amber: "#F5B544",
        // model edge / money — positive vs negative ONLY
        edge: "#34D399",
        coral: "#F87171",
      },
      boxShadow: {
        // soft outer glow on the active league chip + primary buttons
        glow: "0 0 0 1px var(--brand), 0 0 16px 0 var(--brand-glow)",
        "glow-sm": "0 0 12px 0 var(--brand-glow)",
        "edge-glow": "0 0 14px 0 rgba(52,211,153,0.35)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      borderRadius: {
        "2xl": "1rem",
      },
      backgroundImage: {
        "app-gradient": "linear-gradient(180deg, #0B0F14 0%, #070A0E 100%)",
      },
    },
  },
  plugins: [],
};
