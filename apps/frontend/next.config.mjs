/**
 * The SentinelOps services the dashboard reads from run on separate ports with
 * no CORS headers and no `/api/v1` gateway. Next.js server-side rewrites proxy
 * them under a single same-origin `/api/*` prefix, so the browser never makes a
 * cross-origin request and no backend change is needed (beyond the Phase 10.1
 * auth routes added to apps/api, which already live under `/api/v1/auth`).
 *
 * Override the targets with env vars (see .env.example) — in docker-compose they
 * point at the internal service names.
 *
 * @type {import('next').NextConfig}
 */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    const incident = process.env.INCIDENT_API_URL || "http://localhost:8002";
    const rca = process.env.RCA_API_URL || "http://localhost:8004";
    const remediation = process.env.REMEDIATION_API_URL || "http://localhost:8005";
    const detector = process.env.DETECTOR_API_URL || "http://localhost:8003";
    const auth = process.env.AUTH_API_URL || "http://localhost:8000";
    return [
      { source: "/api/incident/:path*", destination: `${incident}/:path*` },
      { source: "/api/rca/:path*", destination: `${rca}/:path*` },
      { source: "/api/remediation/:path*", destination: `${remediation}/:path*` },
      { source: "/api/detector/:path*", destination: `${detector}/:path*` },
      // apps/api already namespaces its auth routes under /api/v1/auth.
      { source: "/api/auth/:path*", destination: `${auth}/api/v1/auth/:path*` },
    ];
  },
};

export default nextConfig;
