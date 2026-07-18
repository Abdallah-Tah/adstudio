import type { NextConfig } from "next";

// Proxy API calls through the Next server so one origin (and one Cloudflare
// tunnel to :3000) serves both the editor and the backend on :8300.
const nextConfig: NextConfig = {
  // Project creation runs stages 1-4 synchronously (~30-50s for a multi-photo
  // batch). Next's proxy default aborts rewrites at 30s ("socket hang up" ->
  // 500), so give the create request enough headroom. Still under Cloudflare's
  // ~100s origin limit.
  experimental: { proxyTimeout: 120_000 },
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
