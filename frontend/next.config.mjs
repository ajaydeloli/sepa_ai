/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Proxy all /api/* calls to FastAPI — avoids CORS and hides the key in prod.
  // Update the destination domain in vercel.json for Vercel deployments;
  // this rewrite is used for local dev and self-hosted Node deployments.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },

  // Required for static export on some hosting targets; harmless on Vercel.
  images: { unoptimized: true },
};

export default nextConfig;
