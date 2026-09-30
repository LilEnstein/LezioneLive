import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./companion.html", "./src/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      fontFamily: {
        // Font hệ thống (không tải font từ Internet: app chạy hoàn toàn cục bộ).
        sans: ['"Segoe UI"', '"Noto Sans"', '"Helvetica Neue"', "system-ui", "sans-serif"],
      },
      colors: {
        brand: {
          50: "#eef6ff",
          100: "#d9ebff",
          200: "#bcdcff",
          300: "#8ec6ff",
          400: "#59a5ff",
          500: "#3382fb",
          600: "#1d63f0",
          700: "#164edd",
          800: "#1840b3",
          900: "#1a3a8d",
          950: "#132456",
        },
      },
      keyframes: {
        blink: { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0" } },
        pulseDot: { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
      },
      animation: {
        blink: "blink 1s step-end infinite",
        pulseDot: "pulseDot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [typography],
};
