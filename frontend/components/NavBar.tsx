"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { BarChart2, Bookmark, Briefcase, Menu, Search, X, Zap } from "lucide-react";
import { api } from "@/lib/api";

const NAV_LINKS = [
  { href: "/",          label: "Dashboard", icon: BarChart2 },
  { href: "/screener",  label: "Screener",  icon: Search    },
  { href: "/watchlist", label: "Watchlist", icon: Bookmark  },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
];

export default function NavBar() {
  const pathname   = usePathname();
  const [open, setOpen] = useState(false);

  const { data: health } = useSWR("nav-health", () => api.getHealth(), {
    refreshInterval: 30_000,
    revalidateOnFocus: false,
  });
  const apiOk = health?.data?.status === "ok";

  const handleRun = async () => {
    try {
      await api.triggerRun("all");
      alert("Pipeline run triggered!");
    } catch {
      alert("Failed to trigger run — check API connection.");
    }
  };

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <nav className="sticky top-0 z-50 bg-slate-950/90 backdrop-blur border-b border-slate-800">
      <div className="max-w-screen-2xl mx-auto px-4 h-14 flex items-center justify-between">

        {/* Brand */}
        <Link href="/" className="flex items-center gap-2 font-bold text-sm tracking-wide shrink-0">
          <span className="text-blue-400 text-base">📈</span>
          <span className="text-slate-100">SEPA</span>
          <span className="text-slate-500 font-normal">AI</span>
        </Link>

        {/* Desktop links */}
        <div className="hidden sm:flex items-center gap-1">
          {NAV_LINKS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                isActive(href)
                  ? "bg-blue-700/20 text-blue-300"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
              }`}
            >
              <Icon size={14} />
              {label}
            </Link>
          ))}
        </div>

        {/* Right: API status + Run + hamburger */}
        <div className="flex items-center gap-3">
          {/* API health indicator */}
          <div className="flex items-center gap-1.5 text-xs text-slate-400" title={apiOk ? "API online" : "API offline"}>
            <span className={`w-2 h-2 rounded-full ${apiOk ? "bg-green-500 animate-pulse" : "bg-red-500"}`} />
            <span className="hidden sm:inline">{apiOk ? "Online" : "Offline"}</span>
          </div>

          <button
            onClick={handleRun}
            className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-700 hover:bg-blue-600 rounded-lg transition-colors font-medium"
          >
            <Zap size={12} /> Run Pipeline
          </button>

          {/* Mobile hamburger */}
          <button
            className="sm:hidden p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
            onClick={() => setOpen((v) => !v)}
            aria-label="Toggle menu"
          >
            {open ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </div>

      {/* Mobile drawer */}
      {open && (
        <div className="sm:hidden border-t border-slate-800 bg-slate-950 px-4 py-3 space-y-1">
          {NAV_LINKS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              onClick={() => setOpen(false)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                isActive(href)
                  ? "bg-blue-700/20 text-blue-300"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
              }`}
            >
              <Icon size={16} />
              {label}
            </Link>
          ))}
          <button
            onClick={() => { handleRun(); setOpen(false); }}
            className="w-full mt-2 flex items-center justify-center gap-1.5 px-3 py-2 text-sm bg-blue-700 hover:bg-blue-600 rounded-lg transition-colors font-medium"
          >
            <Zap size={14} /> Run Pipeline
          </button>
        </div>
      )}
    </nav>
  );
}
