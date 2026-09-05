import type { Config } from "tailwindcss";

// Severity / status colours are applied as complete literal class strings in
// app/components/Badge.tsx (so Tailwind's JIT keeps them) — not as dynamic
// tokens here.
const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: { extend: {} },
  plugins: [],
};

export default config;
