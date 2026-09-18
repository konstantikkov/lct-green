/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0b0f14",
          900: "#0f141b",
          850: "#131a22",
          800: "#161e28",
          700: "#1c2531",
          600: "#26303c",
          500: "#3d4654",
          400: "#5b6675",
          300: "#8b95a5",
          200: "#b6bfcc",
          100: "#dde3ec",
        },
        accent: "#22c55e",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["11px", "14px"],
      },
    },
  },
  plugins: [],
};
