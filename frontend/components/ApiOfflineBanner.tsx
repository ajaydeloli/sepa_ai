/**
 * components/ApiOfflineBanner.tsx
 * Shows a persistent warning banner when the API health check fails.
 * Drop-in: render near top of any page that uses SWR API calls.
 *
 * Usage:
 *   <ApiOfflineBanner />
 *
 * Relies on the same SWR key ("nav-health") used by NavBar so health data
 * is shared from the cache — no extra network request.
 */

"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { WifiOff } from "lucide-react";

export default function ApiOfflineBanner() {
  const { data, error } = useSWR("nav-health", () => api.getHealth(), {
    refreshInterval: 30_000,
    revalidateOnFocus: true,
    // Don't throw — we handle failure with the banner
    shouldRetryOnError: true,
  });

  const isOffline = error || data?.data?.status !== "ok";

  if (!isOffline) return null;

  return (
    <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-red-950/60 border border-red-800/50 text-red-300 text-sm no-print">
      <WifiOff size={15} className="shrink-0 text-red-400" />
      <span>
        <span className="font-semibold">API offline</span> — cannot reach the FastAPI backend.
        Data shown may be stale. Check that the server is running at{" "}
        <code className="text-red-200 bg-red-900/40 px-1 rounded text-xs">
          {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}
        </code>
        .
      </span>
    </div>
  );
}
