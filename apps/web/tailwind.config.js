/** @type {import('tailwindcss').Config} */

/**
 * The `ink` scale is a semantic neutral ramp driven by CSS variables
 * (see index.css): ink-950 = app background … ink-100 = primary text.
 * Light theme is the default; `.dark` on <html> flips the ramp.
 * Colored accents (emerald/amber/red/sky) use explicit `dark:` variants.
 */
const ink = {};
for (const step of [100, 200, 300, 400, 500, 600, 700, 800, 850, 900, 950]) {
  ink[step] = `rgb(var(--ink-${step}) / <alpha-value>)`;
}

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        ink,
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
