import type { Metadata } from "next";
import "./globals.css";
import { AppNav } from "@/components/app-nav";
import { HistoryDockProvider } from "@/components/history-dock";

// Every view is DB-backed and behind scrimmage's admin check; never prerender.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Pkrtask",
  description: "MIT Pokerbots admin task planner",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <HistoryDockProvider>
          <AppNav />
          <main className="min-w-0">{children}</main>
        </HistoryDockProvider>
      </body>
    </html>
  );
}
