"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type AuthUser, currentUser, logout } from "@/app/lib/auth";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/incidents", label: "Incidents" },
  { href: "/models", label: "Models" },
];

const ROLE_BADGE: Record<string, string> = {
  admin: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  approver: "bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-500/30",
  viewer: "bg-gray-500/15 text-gray-300 ring-1 ring-inset ring-gray-500/30",
};

export function Nav() {
  const pathname = usePathname();
  const [user, setUser] = useState<AuthUser | null>(null);
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  useEffect(() => {
    setUser(currentUser());
  }, [pathname]);

  if (pathname === "/login") {
    return (
      <header className="border-b border-gray-800 bg-gray-950/60 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center px-4 font-semibold text-gray-100">
          <span className="mr-2 inline-block h-2.5 w-2.5 rounded-full bg-emerald-400" />
          SentinelOps
        </div>
      </header>
    );
  }

  return (
    <header className="border-b border-gray-800 bg-gray-950/60 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold text-gray-100">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-400" />
          SentinelOps
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`rounded-md px-3 py-1.5 transition ${
                active(l.href) ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-100"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-xs">
          {user ? (
            <>
              <span className="text-gray-400">{user.username}</span>
              <span
                className={`rounded-md px-2 py-0.5 font-medium ${ROLE_BADGE[user.role] ?? ROLE_BADGE.viewer}`}
              >
                {user.role}
              </span>
              <button onClick={logout} className="text-gray-500 hover:text-gray-100">
                sign out
              </button>
            </>
          ) : (
            <span className="text-gray-600">operator dashboard · Phase 10</span>
          )}
        </div>
      </div>
    </header>
  );
}
