import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Quality tier colours (SEPA setup grades)
        "quality-aplus": "#16a34a",  // green-600
        "quality-a":     "#22c55e",  // green-500
        "quality-b":     "#eab308",  // yellow-500
        "quality-c":     "#f97316",  // orange-500
        "quality-fail":  "#ef4444",  // red-500
        // Chart / stage colours
        "stage-2": "#3b82f6",        // blue-500  (buyable)
        "stage-1": "#94a3b8",        // slate-400
        "stage-3": "#f59e0b",        // amber-500
        "stage-4": "#ef4444",        // red-500
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
