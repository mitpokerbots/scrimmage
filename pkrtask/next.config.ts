import type { NextConfig } from "next";
import { BASE_PATH } from "./lib/base-path";

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  basePath: BASE_PATH,
  // A stray lockfile exists in the home directory; pin the workspace root.
  turbopack: { root: __dirname },
  poweredByHeader: false,
  // Compression buffers the live-collaboration event stream (app/api/realtime).
  compress: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
  // In production Apache serves scrimmage and Pkrtask on one origin. In dev,
  // send everything outside /pkrtask (login, /static) to the Flask dev server
  // so its session cookie is set on this origin too.
  async rewrites() {
    if (!isDev) return [];
    const scrimmage = process.env.SCRIMMAGE_INTERNAL_URL ?? "http://127.0.0.1:8000";
    return {
      beforeFiles: [],
      afterFiles: [],
      fallback: [{ source: "/:path*", destination: `${scrimmage}/:path*`, basePath: false }],
    };
  },
};

export default nextConfig;
