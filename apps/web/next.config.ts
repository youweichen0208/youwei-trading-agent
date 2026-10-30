import type { NextConfig } from "next";

const config: NextConfig = {
  distDir: process.env.YTA_E2E_ISOLATED === "1" ? ".next-e2e" : ".next",
  typescript: { tsconfigPath: process.env.YTA_E2E_ISOLATED === "1" ? "tsconfig.e2e.json" : "tsconfig.json" },
  output: process.env.YTA_STANDALONE === "1" ? "standalone" : undefined,
  poweredByHeader: false,
  transpilePackages: ["@yta/contracts"],
  async rewrites() {
    return [{ source: "/api/yta/:path*", destination: `${process.env.YTA_BFF_ORIGIN ?? "http://127.0.0.1:3100"}/api/yta/:path*` }];
  },
};
export default config;
