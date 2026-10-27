/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#15212b",
        cream: "#f7f4ed",
        signal: "#ffcd38",
        success: "#0b8f66",
        danger: "#c64242",
        shell: "#f5f7fb",
        "brand-blue": "#2563eb",
      },
      boxShadow: { card: "0 10px 35px rgba(21,33,43,.08)", glow: "0 0 28px rgba(255,205,56,.22)" },
    },
  },
  plugins: [],
};
