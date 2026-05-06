/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm cream paper canvas — pure white reads cold and clinical for
        // a tool you stare at during multi-hour rule-tuning sessions.
        paper: {
          50: "#fefcf6",
          100: "#fbf6e8",
          200: "#f5edd6",
          300: "#ece1c0",
          400: "#dcceaa",
          500: "#bda77f",
        },
        // Warm grays — used as TYPE colors, light-mode-tuned. The 100/200
        // shades are background fills, 600+ are text.
        ink: {
          50: "#fbf8f1",
          100: "#f3ecdb",
          200: "#e3d8be",
          300: "#cbbb95",
          400: "#a89a72",
          500: "#7a6f55",
          600: "#534b39",
          700: "#3a3429",
          800: "#26221a",
          900: "#161310",
          950: "#0c0b09",
        },
        // Wordmark gold (#d4a017). gold-500 (#a87e0c) is the WCAG-readable
        // gold for type on cream. Use gold-500 for typography, gold-DEFAULT
        // for fills / borders / score values.
        gold: {
          DEFAULT: "#d4a017",
          50: "#fdf7e3",
          100: "#fbecb6",
          200: "#f4d97a",
          300: "#e8c044",
          400: "#d4a017",
          500: "#a87e0c",
          600: "#7a5b09",
          700: "#523c06",
        },
      },
      fontFamily: {
        display: ['"Fraunces"', "ui-serif", "Georgia", "serif"],
        sans: ['"Geist"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "Menlo", "monospace"],
      },
      letterSpacing: {
        tightish: "-0.01em",
        eyebrow: "0.16em",
      },
      boxShadow: {
        gold: "0 0 0 1px rgba(212,160,23,0.55), 0 1px 8px -1px rgba(212,160,23,0.18)",
        // Real paper shadows — soft, warm-toned, not the typical neutral gray.
        card: "0 1px 0 0 rgba(82,60,6,0.04), 0 1px 2px 0 rgba(82,60,6,0.06)",
        "card-hover":
          "0 2px 0 0 rgba(82,60,6,0.06), 0 4px 12px -2px rgba(82,60,6,0.10)",
      },
    },
  },
  plugins: [],
};
