/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // base + surfaces are CSS variables so the light-mode toggle can flip them
        bg: "var(--c-bg)",
        "bg-deep": "var(--c-bg-deep)",
        surface: "var(--c-surface)",
        "surface-hover": "var(--c-surface-hover)",
        // primary accent (violet) — active tab/chip, Kelly, progress, slate glow
        violet: "#7C5CFF",
        // brand-mark gradient stops (lightning bolt)
        "brand-cyan": "#22D3EE",
        "brand-blue": "#3B82F6",
        // model leans
        mint: "#34D399", // OVER / positive
        coral: "#F87171", // UNDER / negative
        // utility
        "util-blue": "#5B9BD5",
        amber: "#F5B544",
        // text (CSS vars — flip in light mode)
        ink: "var(--c-ink)",
        "ink-dim": "var(--c-ink-dim)",
      },
      boxShadow: {
        "violet-glow": "0 0 0 1px rgba(124,92,255,0.7), 0 0 18px 0 rgba(124,92,255,0.35)",
        "violet-soft": "0 0 14px 0 rgba(124,92,255,0.30)",
        "mint-glow": "0 0 12px 0 rgba(52,211,153,0.30)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      backgroundImage: {
        "brand-bolt": "linear-gradient(135deg, #22D3EE 0%, #3B82F6 100%)",
        // near-black base with a subtle violet→blue glow at the top
        "app-glow":
          "radial-gradient(1100px 480px at 50% -260px, rgba(124,92,255,0.18) 0%, rgba(59,130,246,0.08) 38%, rgba(11,11,18,0) 70%), linear-gradient(180deg, #0B0B12 0%, #07070D 100%)",
      },
    },
  },
  plugins: [],
};
