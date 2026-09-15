/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0b0f14",
          panel: "#121820",
          raised: "#182028",
          border: "#232b36",
        },
        ink: {
          primary: "#e6edf3",
          secondary: "#8b96a5",
          muted: "#5b6672",
        },
        brand: {
          DEFAULT: "#22d3ee",
          dim: "#0e7490",
        },
        risk: {
          low: "#22c55e",
          medium: "#f59e0b",
          high: "#ef4444",
        },
        status: {
          healthy: "#22c55e",
          warning: "#f59e0b",
          critical: "#ef4444",
          info: "#38bdf8",
        },
      },
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
