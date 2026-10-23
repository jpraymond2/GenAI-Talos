/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f4f6f5",
          100: "#e4e9e7",
          200: "#c8d2ce",
          300: "#9aada5",
          400: "#6b857b",
          500: "#4f6a61",
          600: "#3d534c",
          700: "#334440",
          800: "#2b3835",
          900: "#252f2d",
          950: "#121916",
        },
        ember: {
          400: "#e8a54b",
          500: "#d4892a",
          600: "#b56d1c",
        },
        sea: {
          400: "#3d9b8f",
          500: "#2a7f74",
          600: "#1f655c",
        },
      },
      fontFamily: {
        display: ['"Fraunces"', "Georgia", "serif"],
        sans: ['"Outfit"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 rgba(18, 25, 22, 0.06), 0 18px 40px -24px rgba(18, 25, 22, 0.35)",
      },
      backgroundImage: {
        grain:
          "radial-gradient(ellipse at 20% 0%, rgba(61,155,143,0.08), transparent 50%), radial-gradient(ellipse at 90% 10%, rgba(212,137,42,0.07), transparent 45%), linear-gradient(165deg, #f7f5f0 0%, #eef2ef 45%, #e7ece9 100%)",
      },
    },
  },
  plugins: [],
};
