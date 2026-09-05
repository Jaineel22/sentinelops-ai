import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "./components/Nav";
import { AuthGuard } from "./components/AuthGuard";

export const metadata: Metadata = {
  title: "SentinelOps — operator dashboard",
  description:
    "Incidents, root-cause analyses and the human remediation-approval flow for the SentinelOps AI platform.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <Nav />
        <main className="mx-auto max-w-6xl px-4 py-6">
          <AuthGuard>{children}</AuthGuard>
        </main>
      </body>
    </html>
  );
}
