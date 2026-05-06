/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm near-black canvas — pure black reads cold and clinical.
        ink: {
          950: "#0c0c0c",
          900: "#111110",
          800: "#1a1a18",
          700: "#26241f",
          600: "#3a362d",
          500: "#5a5247",
          400: "#827767",
          300: "#a89a83",
          200: "#cabba0",
          100: "#e8dcc4",
          50: "#f5ecd6",
        },
        // The wordmark gold from packages/python/goldenmatch/assets/social-preview.png.
        // Reserved for: selection edge, score values, focus rings, primary affordances.
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
        // Soft golden glow used on focus/active states only.
        gold: "0 0 0 1px rgba(212,160,23,0.45), 0 0 18px -2px rgba(212,160,23,0.25)",
      },
      backgroundImage: {
        // Faint top/bottom gold strips echoing the social preview frame.
        "frame-strips":
          "linear-gradient(to bottom, #d4a017 0, #d4a017 2px, transparent 2px, transparent calc(100% - 2px), #d4a017 calc(100% - 2px), #d4a017 100%)",
      },
    },
  },
  plugins: [],
};
