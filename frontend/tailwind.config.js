/** @type {import('tailwindcss').Config} */
import { palette } from "./src/theme.js";

export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  darkMode: "class",
  theme: {
    extend: {
      // Palette lives in src/theme.js so charts and CSS can never drift apart.
      colors: palette,
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 2px 0 rgba(0,0,0,0.4)",
      },
    },
  },
  plugins: [],
};
