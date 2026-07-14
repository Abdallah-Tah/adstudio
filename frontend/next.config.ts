import type { NextConfig } from "next";

// Proxy API calls through the Next server so one origin (and one Cloudflare
// tunnel to :3000) serves both the editor and the backend on :8300.
const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL ?? "http://localhost:8300"}/:path*`,
      },
    ];
  },
};
export default nextConfig;
