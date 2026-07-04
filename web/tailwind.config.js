/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // base (CSS vars so a light mode could flip them; default = quant dark)
        bg: "var(--c-bg)",
        "bg-deep": "var(--c-bg-deep)",
        // card backgrounds
        "card-rec": "#12101D", // recommended / edge picks
        "card-std": "#0E0E16", // standard picks
        "card-soft": "#0D1418", // soft-lines (cyan) cards
        panel: "rgba(255,255,255,0.018)", // KPI / control / chart panels
        // accent (violet) — brand, edge tiers, Kelly, links, selection
        accent: "#7C5CFF",
        "accent-soft": "rgba(124,92,255,0.14)",
        "accent-border": "rgba(124,92,255,0.40)",
        // secondary cyan — soft lines / market signal / CLV / weather
        cyan: "#22D3EE",
        // model leans / outcomes
        pos: "#34D399", // over / positive edge / win
        neg: "#F87171", // under / negative edge / loss
        warn: "#F5B544", // breakeven / disclaimer
        // text ramp
        ink: "#ECECF2",
        "ink-2": "#9A9AA8",
        "ink-3": "#7A7A88",
        "ink-4": "#565663",
        "ink-5": "#4F4F5C",
        // hairline
        hair: "rgba(255,255,255,0.06)",
      },
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["'Geist Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        rec: "0 0 30px rgba(124,92,255,0.07)", // recommended-card glow
        btn: "0 0 18px rgba(124,92,255,0.14)", // primary button glow
        dot: "0 0 10px rgba(124,92,255,0.35)", // gauge dot glow
      },
      backgroundImage: {
        brand: "linear-gradient(135deg,#22D3EE 0%,#7C5CFF 100%)",
        "app-glow":
          "radial-gradient(1200px 560px at 50% -300px, rgba(124,92,255,0.17) 0%, rgba(59,130,246,0.06) 42%, rgba(9,9,14,0) 74%), linear-gradient(180deg,#0A0A11 0%,#06060B 100%)",
      },
    },
  },
  plugins: [],
};
