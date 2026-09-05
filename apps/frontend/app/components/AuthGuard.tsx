"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { fetchMe, isAuthenticated } from "@/app/lib/auth";

/** Wraps the whole app (see layout.tsx). `/login` renders unguarded; every
 * other route redirects to `/login?next=<path>` unless a token is present and
 * still valid server-side (a stale/tampered token gets bounced by /auth/me,
 * not just trusted because it exists in localStorage). */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (pathname === "/login") {
      setReady(true);
      return;
    }
    if (!isAuthenticated()) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }
    let cancelled = false;
    fetchMe()
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch(() => {
        if (!cancelled) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (pathname === "/login") return <>{children}</>;
  if (!ready) return <div className="p-6 text-sm text-gray-500">Checking session…</div>;
  return <>{children}</>;
}
